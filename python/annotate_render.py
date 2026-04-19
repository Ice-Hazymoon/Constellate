#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from annotate_geometry import clip_segment_to_bounds, crop_bounds, point_distance_squared
from annotate_options import overlay_layer_enabled
from annotate_scene import (
    LANCZOS_RESAMPLING,
    boxes_overlap,
    clamp_text,
    compute_label_leader_segment,
    dso_style,
    load_font,
    overlay_supersample_scale,
    place_label,
    scale_constellation_overlays,
    scale_crop_candidate,
    scale_positioned_overlay_items,
)
from annotate_types import CropCandidate


def scale_overlay_scene(scene: dict[str, Any], scale: int) -> dict[str, Any]:
    if scale == 1:
        return deepcopy(scene)

    scaled = deepcopy(scene)
    scaled["image_width"] = int(round(float(scaled["image_width"]) * scale))
    scaled["image_height"] = int(round(float(scaled["image_height"]) * scale))

    crop = scaled["crop"]
    for key in ("x", "y", "width", "height"):
        crop[key] = int(round(float(crop[key]) * scale))

    for key in ("left", "top", "right", "bottom"):
        scaled["bounds"][key] = float(scaled["bounds"][key]) * scale

    for line in scaled["constellation_lines"]:
        for key in ("x1", "y1", "x2", "y2"):
            line[key] = float(line[key]) * scale
        line["line_width"] = max(1, int(round(float(line["line_width"]) * scale)))

    for marker in scaled["deep_sky_markers"]:
        for key in ("x", "y"):
            marker[key] = float(marker[key]) * scale
        marker["radius"] = max(1, int(round(float(marker["radius"]) * scale)))
        marker["line_width"] = max(1, int(round(float(marker["line_width"]) * scale)))

    for marker in scaled["star_markers"]:
        for key in ("x", "y"):
            marker[key] = float(marker[key]) * scale
        marker["radius"] = max(1, int(round(float(marker["radius"]) * scale)))

    for key in ("deep_sky_labels", "constellation_labels", "star_labels"):
        for label in scaled[key]:
            for coord in ("x", "y"):
                label[coord] = float(label[coord]) * scale
            label["font_size"] = max(1, int(round(float(label["font_size"]) * scale)))
            label["stroke_width"] = max(1, int(round(float(label["stroke_width"]) * scale)))
            if label.get("leader"):
                for coord in ("x1", "y1", "x2", "y2"):
                    label["leader"][coord] = float(label["leader"][coord]) * scale
                label["leader"]["line_width"] = max(1, int(round(float(label["leader"]["line_width"]) * scale)))

    return scaled


def draw_dso_marker(
    draw: ImageDraw.ImageDraw,
    item: dict[str, Any],
    radius: int,
    line_width: int,
) -> tuple[int, int, int, int]:
    x_value = item["x"]
    y_value = item["y"]
    marker, color = dso_style(item)
    return draw_dso_marker_primitive(draw, marker, x_value, y_value, radius, line_width, color)


def draw_dso_marker_primitive(
    draw: ImageDraw.ImageDraw,
    marker: str,
    x_value: float,
    y_value: float,
    radius: int,
    line_width: int,
    color: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    bounds = (
        int(round(x_value - radius)),
        int(round(y_value - radius)),
        int(round(x_value + radius)),
        int(round(y_value + radius)),
    )
    width = max(1, line_width)

    if marker == "square":
        draw.rectangle(bounds, outline=color, width=width)
    elif marker == "crossed_circle":
        draw.ellipse(bounds, outline=color, width=width)
        draw.line((x_value - radius, y_value, x_value + radius, y_value), fill=color, width=width)
        draw.line((x_value, y_value - radius, x_value, y_value + radius), fill=color, width=width)
    elif marker == "ring":
        draw.ellipse(bounds, outline=color, width=width)
        inner = max(2, radius // 2)
        draw.ellipse(
            (
                x_value - inner,
                y_value - inner,
                x_value + inner,
                y_value + inner,
            ),
            outline=color,
            width=width,
        )
    elif marker == "x_circle":
        draw.ellipse(bounds, outline=color, width=width)
        draw.line((x_value - radius, y_value - radius, x_value + radius, y_value + radius), fill=color, width=width)
        draw.line((x_value - radius, y_value + radius, x_value + radius, y_value - radius), fill=color, width=width)
    elif marker == "hexagon":
        vertical = radius * 0.86
        horizontal = radius * 0.5
        points = [
            (x_value - horizontal, y_value - vertical),
            (x_value + horizontal, y_value - vertical),
            (x_value + radius, y_value),
            (x_value + horizontal, y_value + vertical),
            (x_value - horizontal, y_value + vertical),
            (x_value - radius, y_value),
        ]
        draw.polygon(points, outline=color, width=width)
    elif marker == "diamond":
        points = [
            (x_value, y_value - radius),
            (x_value + radius, y_value),
            (x_value, y_value + radius),
            (x_value - radius, y_value),
        ]
        draw.polygon(points, outline=color, width=width)
    else:
        draw.ellipse(bounds, outline=color, width=width)

    return bounds


def draw_label_leader(
    draw: ImageDraw.ImageDraw,
    anchor_x: float,
    anchor_y: float,
    label_position: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    color: tuple[int, int, int, int],
    stroke_width: int = 2,
) -> None:
    segment = compute_label_leader_segment(
        draw,
        anchor_x,
        anchor_y,
        label_position,
        text,
        font,
        stroke_width=stroke_width,
    )
    if segment is None:
        return
    draw.line(segment, fill=color, width=1)


def render_overlay_scene_rgba(image_size: tuple[int, int], scene: dict[str, Any]) -> Image.Image:
    overlay = Image.new("RGBA", image_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_cache: dict[int, ImageFont.ImageFont] = {}

    def font_for_size(size: int) -> ImageFont.ImageFont:
        normalized_size = max(1, int(size))
        cached = font_cache.get(normalized_size)
        if cached is None:
            cached = load_font(normalized_size)
            font_cache[normalized_size] = cached
        return cached

    for line in scene["constellation_lines"]:
        draw.line(
            (line["x1"], line["y1"], line["x2"], line["y2"]),
            fill=tuple(line["rgba"]),
            width=int(line["line_width"]),
        )

    for marker in scene["deep_sky_markers"]:
        draw_dso_marker_primitive(
            draw,
            marker["marker"],
            marker["x"],
            marker["y"],
            int(marker["radius"]),
            int(marker["line_width"]),
            tuple(marker["rgba"]),
        )

    for marker in scene["star_markers"]:
        draw.ellipse(
            (
                marker["x"] - marker["radius"],
                marker["y"] - marker["radius"],
                marker["x"] + marker["radius"],
                marker["y"] + marker["radius"],
            ),
            fill=tuple(marker["fill_rgba"]),
            outline=tuple(marker["outline_rgba"]),
        )

    for collection_name in ("deep_sky_labels", "constellation_labels", "star_labels"):
        for label in scene[collection_name]:
            leader = label.get("leader")
            if leader:
                draw.line(
                    (leader["x1"], leader["y1"], leader["x2"], leader["y2"]),
                    fill=tuple(leader["rgba"]),
                    width=int(leader["line_width"]),
                )
            draw.text(
                (label["x"], label["y"]),
                label["text"],
                font=font_for_size(label["font_size"]),
                fill=tuple(label["text_rgba"]),
                stroke_width=int(label["stroke_width"]),
                stroke_fill=tuple(label["stroke_rgba"]),
            )

    return overlay


def render_overlay_scene(base_image: Image.Image, overlay_scene: dict[str, Any]) -> Image.Image:
    supersample = overlay_supersample_scale(base_image.width, base_image.height)

    if supersample > 1:
        overlay = render_overlay_scene_rgba(
            (base_image.width * supersample, base_image.height * supersample),
            scale_overlay_scene(overlay_scene, supersample),
        ).resize(base_image.size, LANCZOS_RESAMPLING)
    else:
        overlay = render_overlay_scene_rgba(base_image.size, overlay_scene)

    return Image.alpha_composite(base_image.copy().convert("RGBA"), overlay).convert("RGB")


def render_overlay_rgba(
    image_size: tuple[int, int],
    constellations: list[dict[str, Any]],
    named_stars: list[dict[str, Any]],
    deep_sky_objects: list[dict[str, Any]],
    crop: CropCandidate,
    overlay_options: dict[str, Any],
) -> Image.Image:
    image_width, image_height = image_size
    overlay = Image.new("RGBA", image_size, (0, 0, 0, 0))
    line_overlay = Image.new("RGBA", image_size, (0, 0, 0, 0))
    line_draw = ImageDraw.Draw(line_overlay)

    min_dimension = min(image_width, image_height)
    line_width = max(1, min_dimension // 600)
    render_bounds = crop_bounds(crop)

    if overlay_layer_enabled(overlay_options, "constellation_lines"):
        for constellation in constellations:
            line_color = (212, 222, 236, 135 if constellation["show_label"] else 92)
            for segment in constellation["segments"]:
                clipped_segment = clip_segment_to_bounds(
                    segment["start"]["x"],
                    segment["start"]["y"],
                    segment["end"]["x"],
                    segment["end"]["y"],
                    render_bounds[0],
                    render_bounds[1],
                    render_bounds[2],
                    render_bounds[3],
                )
                if clipped_segment is None:
                    continue
                if point_distance_squared(*clipped_segment) < 1.0:
                    continue
                line_draw.line(
                    clipped_segment,
                    fill=line_color,
                    width=line_width,
                )

    overlay = Image.alpha_composite(overlay, line_overlay)
    draw = ImageDraw.Draw(overlay)
    occupied_boxes: list[tuple[float, float, float, float]] = []

    constellation_font = load_font(max(18, min_dimension // 52))
    dso_font = load_font(max(14, min_dimension // 74))
    star_font = load_font(max(12, min_dimension // 84))
    dso_radius = max(4, min_dimension // 250)
    star_radius = max(2, min_dimension // 320)

    show_dso_markers = overlay_layer_enabled(overlay_options, "deep_sky_markers")
    show_dso_labels = overlay_layer_enabled(overlay_options, "deep_sky_labels")
    show_constellation_labels = overlay_layer_enabled(overlay_options, "constellation_labels")
    show_contextual_labels = overlay_layer_enabled(overlay_options, "contextual_constellation_labels")
    show_star_markers = overlay_layer_enabled(overlay_options, "star_markers")
    show_star_labels = overlay_layer_enabled(overlay_options, "star_labels")
    show_label_leaders = overlay_layer_enabled(overlay_options, "label_leaders")

    for item in deep_sky_objects:
        if show_dso_markers:
            draw_dso_marker(draw, item, dso_radius, line_width)
        if not show_dso_labels:
            continue
        position = place_label(
            draw,
            item["display_label"],
            item["x"],
            item["y"],
            image_width,
            image_height,
            dso_font,
            occupied_boxes,
            offsets=[
                (10.0, -26.0),
                (10.0, 10.0),
                (-112.0, -26.0),
                (-112.0, 10.0),
                (14.0, -42.0),
                (14.0, 24.0),
                (-128.0, -42.0),
                (-128.0, 24.0),
                (8.0, -22.0),
                (8.0, 8.0),
                (-86.0, -22.0),
                (-86.0, 8.0),
            ],
            stroke_width=2,
            bounds=render_bounds,
        )
        if not position:
            continue
        if show_label_leaders:
            draw_label_leader(
                draw,
                item["x"],
                item["y"],
                position,
                item["display_label"],
                dso_font,
                (165, 220, 255, 190),
                stroke_width=2,
            )
        draw.text(
            position,
            item["display_label"],
            font=dso_font,
            fill=(242, 246, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 220),
        )

    if show_constellation_labels:
        for constellation in constellations:
            if not constellation["show_label"]:
                continue
            if not constellation["segments"] and not show_contextual_labels:
                continue
            position = place_label(
                draw,
                constellation["display_name"],
                constellation["label_x"],
                constellation["label_y"],
                image_width,
                image_height,
                constellation_font,
                occupied_boxes,
                offsets=[
                    (10.0, 10.0),
                    (10.0, -34.0),
                    (-56.0, 10.0),
                    (-56.0, -34.0),
                    (12.0, 28.0),
                    (-74.0, 28.0),
                ],
                stroke_width=3,
                bounds=render_bounds,
            )
            if not position:
                continue
            draw.text(
                position,
                constellation["display_name"],
                font=constellation_font,
                fill=(225, 232, 245, 255),
                stroke_width=3,
                stroke_fill=(0, 0, 0, 230),
            )

    for star in named_stars:
        if show_star_markers:
            draw.ellipse(
                (
                    star["x"] - star_radius,
                    star["y"] - star_radius,
                    star["x"] + star_radius,
                    star["y"] + star_radius,
                ),
                fill=(255, 210, 150, 215),
                outline=(255, 255, 255, 210),
            )
        if not show_star_labels:
            continue
        position = place_label(
            draw,
            star["name"],
            star["x"],
            star["y"],
            image_width,
            image_height,
            star_font,
            occupied_boxes,
            offsets=[
                (8.0, -20.0),
                (8.0, 10.0),
                (-86.0, -20.0),
                (-86.0, 10.0),
                (10.0, -34.0),
                (-96.0, -34.0),
                (8.0, -18.0),
                (8.0, 8.0),
                (-74.0, -18.0),
                (-74.0, 8.0),
            ],
            stroke_width=2,
            bounds=render_bounds,
        )
        if not position:
            continue
        if show_label_leaders:
            draw_label_leader(
                draw,
                star["x"],
                star["y"],
                position,
                star["name"],
                star_font,
                (255, 233, 188, 176),
                stroke_width=2,
            )
        draw.text(
            position,
            star["name"],
            font=star_font,
            fill=(250, 244, 236, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 220),
        )

    return overlay


def render_overlay(
    base_image: Image.Image,
    constellations: list[dict[str, Any]],
    named_stars: list[dict[str, Any]],
    deep_sky_objects: list[dict[str, Any]],
    crop: CropCandidate,
    overlay_options: dict[str, Any],
) -> Image.Image:
    supersample = overlay_supersample_scale(base_image.width, base_image.height)

    if supersample > 1:
        overlay = render_overlay_rgba(
            (base_image.width * supersample, base_image.height * supersample),
            scale_constellation_overlays(constellations, supersample),
            scale_positioned_overlay_items(named_stars, supersample),
            scale_positioned_overlay_items(deep_sky_objects, supersample),
            scale_crop_candidate(crop, supersample),
            overlay_options,
        ).resize(base_image.size, LANCZOS_RESAMPLING)
    else:
        overlay = render_overlay_rgba(
            base_image.size,
            constellations,
            named_stars,
            deep_sky_objects,
            crop,
            overlay_options,
        )

    return Image.alpha_composite(base_image.copy().convert("RGBA"), overlay).convert("RGB")
