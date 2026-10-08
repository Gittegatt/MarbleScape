"""Coverage of imagery, distinct from transfer progress and cloud percentage."""
import math


def unavailable():
    return {"data_coverage_percent": None, "data_coverage": {"method": "unavailable"}}


def from_alpha(image):
    if "A" not in image.getbands():
        return unavailable()
    histogram = image.getchannel("A").histogram()
    if any(histogram[1:255]):
        return unavailable()  # Style/fractional opacity is not a binary data mask.
    total = image.width * image.height
    valid = histogram[255]
    return {"data_coverage_percent": round(100 * valid / total, 2),
            "data_coverage": {"method": "copernicus_data_mask", "valid_pixels": valid,
                              "evaluated_pixels": total, "mask_width": image.width,
                              "mask_height": image.height, "scope": "satellite_view_before_overlays"}}


def validate(record):
    if "data_coverage_percent" not in record and "data_coverage" not in record:
        return  # Legacy records have no coverage result.
    percent, detail = record.get("data_coverage_percent"), record.get("data_coverage")
    if not isinstance(detail, dict):
        raise ValueError("Missing data coverage method.")
    if detail == {"method": "unavailable"} and percent is None:
        return
    if set(detail) != {"method", "valid_pixels", "evaluated_pixels", "mask_width", "mask_height", "scope"}:
        raise ValueError("Invalid data coverage fields.")
    valid, total = detail["valid_pixels"], detail["evaluated_pixels"]
    width, height = detail["mask_width"], detail["mask_height"]
    if (any(type(value) is not int for value in (valid, total, width, height))
            or not 0 <= valid <= total or total <= 0 or width <= 0 or height <= 0
            or width * height != total or total > 1_000_000_000
            or detail["method"] != "copernicus_data_mask"
            or detail["scope"] != "satellite_view_before_overlays"
            or type(percent) not in (int, float) or not math.isfinite(percent)
            or percent != round(100 * valid / total, 2)):
        raise ValueError("Inconsistent data coverage result.")


def label(record, missing="Not available"):
    if not record:
        return missing
    try:
        validate(record)
        value = record.get("data_coverage_percent")
        return f"{value:.2f}%" if value is not None else missing
    except (ValueError, TypeError):
        return missing
