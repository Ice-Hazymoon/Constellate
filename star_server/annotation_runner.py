"""
In-process annotation runner.

`AnnotationRunner.__init__` installs `_TrackingPopen` as the global
`subprocess.Popen`. This is intentional and load-bearing for outer-timeout
cancellation: when the FastAPI request times out we need to kill the entire
`solve-field` process group (it spawns `cpulimit`, `augustin`, etc.), and the
only way to reach those grandchildren is `os.killpg`, which in turn requires
the leader to have been started with `start_new_session=True`. The patched
`Popen` sets that flag for any subprocess launched inside an annotation job
(detected via thread-local `job_control`); calls outside a job context — the
startup `python --version` / `solve-field --help` checks, anything in a third
party library — fall through unmodified.

Side effects to be aware of when touching this file:
- Subprocesses inside annotation jobs run in a new process group, so they
  no longer receive terminal SIGINT from the parent's controlling terminal.
- `subprocess.run(..., timeout=...)` inside the pipeline kills only the leader
  on per-attempt timeout; grandchildren are reparented to init and exit shortly
  after. Outer wall-clock cancellation goes through `kill_active_processes` →
  `killpg(SIGKILL)`, which is the clean path.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

import annotate
import annotate_sky_mask
from annotate_localization import canonicalize_locale_tag

from .config import Settings
from .errors import HttpError
from .locale import list_available_locales, list_localization_paths

_ORIGINAL_POPEN = subprocess.Popen
_THREAD_LOCAL = threading.local()
_PATCH_LOCK = threading.Lock()
_SUBPROCESS_TRACKING_INSTALLED = False


class AnnotationCancelledError(RuntimeError):
    pass


@dataclass
class _JobControl:
    active_processes: set[subprocess.Popen[Any]] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancel_requested: threading.Event = field(default_factory=threading.Event)

    def add_process(self, process: subprocess.Popen[Any]) -> None:
        with self.lock:
            self.active_processes.add(process)

    def remove_process(self, process: subprocess.Popen[Any]) -> None:
        with self.lock:
            self.active_processes.discard(process)

    def kill_active_processes(self) -> None:
        with self.lock:
            processes = list(self.active_processes)

        for process in processes:
            try:
                if process.poll() is not None:
                    continue
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass


class _TrackingPopen(_ORIGINAL_POPEN):
    def __init__(self, *args: Any, **kwargs: Any):
        job_control = getattr(_THREAD_LOCAL, "job_control", None)
        self._job_control: _JobControl | None = job_control

        if job_control is not None:
            if job_control.cancel_requested.is_set():
                raise AnnotationCancelledError("annotation cancelled")
            kwargs.setdefault("start_new_session", True)

        super().__init__(*args, **kwargs)

        if job_control is not None:
            job_control.add_process(self)

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> Any:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            if self._job_control is not None:
                self._job_control.remove_process(self)
                if self._job_control.cancel_requested.is_set():
                    raise AnnotationCancelledError("annotation cancelled")


def _install_subprocess_tracking() -> None:
    global _SUBPROCESS_TRACKING_INSTALLED
    with _PATCH_LOCK:
        if _SUBPROCESS_TRACKING_INSTALLED:
            return
        subprocess.Popen = _TrackingPopen
        _SUBPROCESS_TRACKING_INSTALLED = True


@contextmanager
def _job_control_context(job_control: _JobControl):
    previous = getattr(_THREAD_LOCAL, "job_control", None)
    _THREAD_LOCAL.job_control = job_control
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_THREAD_LOCAL, "job_control")
            except AttributeError:
                pass
        else:
            _THREAD_LOCAL.job_control = previous


class AnnotationRunner:
    def __init__(self, settings: Settings):
        _install_subprocess_tracking()
        self._settings = settings
        self._executor = ThreadPoolExecutor(
            max_workers=settings.max_concurrent_jobs,
            thread_name_prefix="star-annotate",
        )
        self._asset_cache: OrderedDict[tuple[str, ...], dict[str, Any]] = OrderedDict()
        self._asset_lock = threading.Lock()
        self._active_jobs = 0
        self._active_jobs_lock = threading.Lock()
        self._localization_paths = list_localization_paths(settings.stardroid_locales_dir)
        self.available_locales = list_available_locales(self._localization_paths)

    @property
    def active_job_count(self) -> int:
        with self._active_jobs_lock:
            return self._active_jobs

    async def preload_async(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.preload)

    def preload(self) -> dict[str, Any]:
        assets = self._load_assets(self._settings.default_locale)
        sky_mask_ready = annotate_sky_mask.preload()
        return {
            "status": "ok",
            "catalog_rows": int(len(assets["catalog"])),
            "constellation_count": int(len(assets["constellations"])),
            "deep_sky_object_count": int(len(assets["deep_sky_objects"])),
            "star_name_count": int(len(assets["star_names"])),
            "sky_mask_ready": bool(sky_mask_ready),
        }

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _cache_key(self, locale: str) -> tuple[str, ...]:
        supplemental = (
            str(self._settings.supplemental_dso_path.resolve())
            if self._settings.supplemental_dso_path.exists()
            else ""
        )
        return (
            str(self._settings.catalog_path.resolve()),
            str(self._settings.modern_constellations_path.resolve()),
            str(self._settings.stardroid_constellations_path.resolve())
            if self._settings.stardroid_constellations_path.exists()
            else "",
            str(self._settings.star_names_path.resolve()),
            str(self._settings.ngc_path.resolve()),
            str(self._settings.stardroid_dso_path.resolve())
            if self._settings.stardroid_dso_path.exists()
            else "",
            *(str(path) for path in self._localization_paths),
            locale,
            supplemental,
        )

    def _load_assets(self, locale: str) -> dict[str, Any]:
        normalized_locale = canonicalize_locale_tag(locale)
        cache_key = self._cache_key(normalized_locale)
        with self._asset_lock:
            cached = self._asset_cache.get(cache_key)
            if cached is not None:
                self._asset_cache.move_to_end(cache_key)
                return cached

            constellation_paths = [self._settings.modern_constellations_path.resolve()]
            if self._settings.stardroid_constellations_path.exists():
                constellation_paths.append(self._settings.stardroid_constellations_path.resolve())

            dso_paths = [self._settings.ngc_path.resolve()]
            if self._settings.stardroid_dso_path.exists():
                dso_paths.append(self._settings.stardroid_dso_path.resolve())

            supplemental_dso_path = (
                self._settings.supplemental_dso_path.resolve()
                if self._settings.supplemental_dso_path.exists()
                else None
            )

            localization_data = annotate.load_localized_names(self._localization_paths, normalized_locale)
            star_names = annotate.load_star_names(
                self._settings.star_names_path.resolve(),
                localization_data.strings,
            )
            constellations = annotate.load_constellations(
                constellation_paths,
                localization_data.strings,
            )
            constellation_name_map = annotate.build_constellation_name_map(constellations)
            deep_sky_objects = annotate.load_deep_sky_objects(
                dso_paths,
                constellation_name_map,
                localization_data.strings,
                supplemental_dso_path,
            )
            required_hips = annotate.collect_required_hips(constellations, star_names)
            catalog = annotate.load_catalog(self._settings.catalog_path.resolve(), required_hips)

            assets = {
                "catalog": catalog,
                "constellations": constellations,
                "deep_sky_objects": deep_sky_objects,
                "star_names": star_names,
                "localization": localization_data,
            }
            self._asset_cache[cache_key] = assets
            self._asset_cache.move_to_end(cache_key)
            while len(self._asset_cache) > self._settings.annotation_worker_asset_cache_size:
                self._asset_cache.popitem(last=False)
            return assets

    def _annotate_blocking(
        self,
        job_control: _JobControl,
        input_path: Path,
        output_image_path: Path | None,
        overlay_options: dict[str, Any],
        locale: str,
    ) -> dict[str, Any]:
        with _job_control_context(job_control):
            assets = self._load_assets(locale)
            return annotate.annotate_image(
                input_path=input_path.resolve(),
                index_dir=self._settings.astrometry_dir.resolve(),
                catalog=assets["catalog"],
                constellations=assets["constellations"],
                deep_sky_objects=assets["deep_sky_objects"],
                star_names=assets["star_names"],
                overlay_options=overlay_options,
                localization=assets["localization"],
                output_image_path=output_image_path.resolve() if output_image_path is not None else None,
            )

    async def run_annotate(
        self,
        *,
        input_path: Path,
        output_image_path: Path | None,
        overlay_options: dict[str, Any],
        locale: str,
        timeout_s: float,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        job_control = _JobControl()

        with self._active_jobs_lock:
            self._active_jobs += 1

        future = loop.run_in_executor(
            self._executor,
            self._annotate_blocking,
            job_control,
            input_path,
            output_image_path,
            overlay_options,
            locale,
        )

        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            job_control.cancel_requested.set()
            job_control.kill_active_processes()
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=5.0)
            except Exception:
                pass
            raise HttpError(504, f"annotation timed out after {timeout_s}s") from exc
        finally:
            with self._active_jobs_lock:
                self._active_jobs -= 1
