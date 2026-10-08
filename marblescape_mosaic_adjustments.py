"""Deterministic display-tone adjustments; never infer validity from RGB colors."""
import math

ALGORITHM = "mosaic-tone-v2"
# Raised whenever tuning changes the pictures of the same algorithm, so cached
# mosaics with Auto tones render once more instead of keeping the old look.
TONE_REVISION = 2
SCOPE = "valid_satellite_pixels_before_fill_and_overlays"
# Auto brightness alone moves the median luminance toward this level. It
# brightens freely but darkens only a little, so dark water and shadows keep
# their detail.
AUTO_BRIGHTNESS_TARGET = 0.42
AUTO_GAMMA_RANGE = (0.5, 1.15)
# After Auto contrast, Auto brightness lifts a picture only while 65 % of its
# pixels are still darker than AUTO_CONTRAST_DARK_LEVEL: then dark land, not
# just a dark sea next to well-lit land, makes up most of it. Bright pictures
# (median above AUTO_BRIGHTNESS_TARGET) are darkened a little, up to gamma 1.15,
# which keeps detail in cloud, snow, salt and sand.
AUTO_CONTRAST_DARK_FRACTION = 0.65
AUTO_CONTRAST_DARK_LEVEL = 0.25
AUTO_CONTRAST_GAMMA_MIN = 0.8
# Auto contrast lets at most this share of the pixels reach black in their
# darkest channel or white in their brightest one.
STRETCH_FRACTION = 0.02
MIN_STRETCH_RANGE = 8
# At most a 1.5-fold stretch: a scene of nothing but cloud or haze must not turn
# its faint texture into a false pattern with visible steps, and a dark mosaic
# rendered at 100 % (most of it sea) must not turn bright and flat.
MAX_STRETCH_FACTOR = 1.5
# The stretch ends slightly below white, so beaches, salt and cloud stay a
# little darker than pure white.
WHITE_POINT = 0.965
# A brightest-channel 98 % quantile at or above this level means the source
# already clipped its highlights; Auto contrast then leaves the picture as it is.
SATURATED_LEVEL = 250
# Above this level the stretch rolls off toward white, so clouds and other
# highlights keep their detail instead of turning flat white.
HIGHLIGHT_KNEE = 0.85
# The roll-off suits a few bright spots (scattered cloud, sand). Large bright
# surfaces (salt flat, cloud deck, snowfield) keep the straight stretch, whose
# steeper highlights show more texture: the roll-off fades out while the share
# of stretched pixels above BRIGHT_LEVEL grows from the first to the second value.
BRIGHT_LEVEL = 0.9
BRIGHT_SHARE_RANGE = (0.08, 0.20)
PIVOT_RANGE = (0.1, 0.9)

_V1_FIELDS = {
    "algorithm", "auto_brightness", "auto_contrast", "contrast_percent", "brightness_percent",
    "evalscript_brightness_percent", "brightness_gain", "contrast_factor", "contrast_offset",
    "evaluated_pixels", "scope",
}
_V2_FIELDS = {
    "algorithm", "auto_brightness", "auto_contrast", "contrast_percent", "brightness_percent",
    "evalscript_brightness_percent", "stretch_low", "stretch_high", "contrast_pivot",
    "midtone_gamma", "evaluated_pixels", "scope",
}


def _quantile(histogram, fraction):
    threshold = max(1, math.ceil(sum(histogram) * fraction))
    count = 0
    for value, amount in enumerate(histogram):
        count += amount
        if count >= threshold:
            return value
    return 0


def _mapped_quantile(histogram, curve, fraction):
    """Luminance quantile (0-1) after applying ``curve`` (0-1 per 0-255 input)."""
    mapped = [0] * 256
    for value, amount in enumerate(histogram):
        mapped[min(255, max(0, round(curve[value] * 255)))] += amount
    return _quantile(mapped, fraction) / 255


def _mapped_median(histogram, curve):
    return _mapped_quantile(histogram, curve, 0.5)


def _shoulder(value):
    """Linear up to the knee, then a soft roll-off that approaches white."""
    if value <= HIGHLIGHT_KNEE:
        return max(0.0, value)
    room = 1 - HIGHLIGHT_KNEE
    return HIGHLIGHT_KNEE + room * (1 - math.exp(-(value - HIGHLIGHT_KNEE) / room))


def _s_curve(x, pivot, contrast):
    """Rational S-curve: slope ``contrast`` at the pivot and its inverse at black
    and white, so shadows and highlights are compressed, never clipped."""
    if x < pivot:
        t = x / pivot
        return pivot * t / (contrast - (contrast - 1) * t)
    u = (1 - x) / (1 - pivot)
    return 1 - (1 - pivot) * u / (contrast - (contrast - 1) * u)


def evalscript_brightness(profile, mosaic=True):
    """Brightness the provider applies; with an Auto option it is applied here instead.

    Only mosaic evalscripts take a brightness; regular layers get theirs here.
    """
    if not mosaic or profile.get("auto_brightness", False) or profile.get("auto_contrast", False):
        return 100
    return profile.get("brightness", 100)


def adjust(image, profile):
    """Apply Auto contrast, manual contrast and midtone brightness to a mosaic.

    Order: stretch (Auto contrast), S-curve around the median (manual contrast,
    without Auto contrast), midtone gamma (Auto brightness, or the manual
    brightness when Auto contrast is on; otherwise the provider applied it).
    One LUT serves all channels, so hues stay; black and white stay fixed.
    """
    auto_brightness = profile.get("auto_brightness", False)
    auto_contrast = profile.get("auto_contrast", False)
    contrast = profile.get("contrast", 100)
    brightness = profile.get("brightness", 100)
    alpha = image.getchannel("A")
    valid = alpha.histogram()[255]
    mask = alpha.point(lambda value: 255 if value == 255 else 0)
    histogram = image.convert("L").histogram(mask=mask) if valid else [0] * 256
    curve = [value / 255 for value in range(256)]
    low, high = 0, 255
    if auto_contrast and valid:
        from PIL import ImageChops
        red, green, blue = image.convert("RGB").split()
        darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
        brightest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        stretch_low = _quantile(darkest.histogram(mask=mask), STRETCH_FRACTION)
        stretch_high = _quantile(brightest.histogram(mask=mask), 1 - STRETCH_FRACTION)
        # The source already clips its highlights to white (cloud in some Landsat
        # true colour scenes): stretching could only raise the black point and
        # darken the land, so Auto contrast leaves the picture as it is.
        saturated = stretch_high >= SATURATED_LEVEL
        if not saturated and stretch_high - stretch_low >= MIN_STRETCH_RANGE:
            width = math.ceil(255 / MAX_STRETCH_FACTOR)
            if stretch_high - stretch_low < width:
                # Widen the range around its middle, inside 0-255.
                stretch_low = min(255 - width, max(0, (stretch_low + stretch_high + 1) // 2 - width // 2))
                stretch_high = stretch_low + width
            low, high = stretch_low, stretch_high
            linear = [(value - low) / (high - low) for value in range(256)]
            bright = sum(amount for value, amount in enumerate(histogram)
                         if linear[value] > BRIGHT_LEVEL) / sum(histogram)
            roll_off = min(1.0, max(0.0, (BRIGHT_SHARE_RANGE[1] - bright)
                                    / (BRIGHT_SHARE_RANGE[1] - BRIGHT_SHARE_RANGE[0])))
            curve = [WHITE_POINT * ((1 - roll_off) * min(1.0, max(0.0, s)) + roll_off * _shoulder(s))
                     for s in linear]
    pivot = 0.5
    if not auto_contrast and contrast != 100:
        factor = contrast / 100
        if valid:
            pivot = round(min(PIVOT_RANGE[1], max(PIVOT_RANGE[0], _mapped_median(histogram, curve))), 4)
        curve = [_s_curve(x, pivot, factor) for x in curve] if factor > 0 else [pivot] * 256
    gamma = 1.0
    if auto_brightness and valid and auto_contrast:
        dark = min(0.98, max(0.02, _mapped_quantile(histogram, curve, AUTO_CONTRAST_DARK_FRACTION)))
        median = min(0.98, max(0.02, _mapped_median(histogram, curve)))
        if dark < AUTO_CONTRAST_DARK_LEVEL:
            gamma = round(max(AUTO_CONTRAST_GAMMA_MIN,
                              math.log(AUTO_CONTRAST_DARK_LEVEL) / math.log(dark)), 4)
        elif median > AUTO_BRIGHTNESS_TARGET:
            gamma = round(min(AUTO_GAMMA_RANGE[1],
                              math.log(AUTO_BRIGHTNESS_TARGET) / math.log(median)), 4)
    elif auto_brightness and valid:
        median = min(0.98, max(0.02, _mapped_median(histogram, curve)))
        gamma = round(min(AUTO_GAMMA_RANGE[1], max(
            AUTO_GAMMA_RANGE[0], math.log(AUTO_BRIGHTNESS_TARGET) / math.log(median))), 4)
    elif not auto_brightness and auto_contrast and brightness != 100:
        gamma = round(100 / brightness, 4)
    curve = [x ** gamma for x in curve]
    record = {
        "algorithm": ALGORITHM, "auto_brightness": auto_brightness,
        "auto_contrast": auto_contrast, "contrast_percent": contrast,
        "brightness_percent": brightness,
        "evalscript_brightness_percent": evalscript_brightness(profile),
        "stretch_low": low, "stretch_high": high, "contrast_pivot": pivot,
        "midtone_gamma": gamma, "evaluated_pixels": valid, "scope": SCOPE,
    }
    lut = [min(255, max(0, round(x * 255))) for x in curve]
    if lut == list(range(256)):
        return image, record
    result = image.convert("RGB").point(lut * 3).convert("RGBA")
    result.putalpha(alpha)
    return result, record


# Tone rules a-f (docs/IMAGE_SOURCES.md, "Tone rules"): combinations of four
# building blocks on top of adjust(), chosen per collection and layer after
# comparing example pictures. The building blocks only act with auto contrast;
# rule a keeps auto brightness neutral.
RULE_ALGORITHM = "tone-rules-v1"
TONE_RULES = {
    "a": {"brightness": False, "shoulder": "fixed", "bright_area": False, "soft_script": False},
    "b": {"brightness": True, "shoulder": "fixed", "bright_area": False, "soft_script": False},
    "c": {"brightness": True, "shoulder": "clipped", "bright_area": False, "soft_script": False},
    "d": {"brightness": True, "shoulder": "fixed", "bright_area": True, "soft_script": False},
    "e": {"brightness": True, "shoulder": "adaptive", "bright_area": False, "soft_script": False},
    "f": {"brightness": True, "shoulder": "adaptive", "bright_area": True, "soft_script": True},
}
# "Bright area": the share of valid pixels whose stretched luminance exceeds
# this level. The adaptive knee and the large bright area adaptation blend
# between the two shares.
BRIGHT_AREA_LEVEL = 0.75
BRIGHT_AREA_RANGE = (0.10, 0.25)
# Adaptive shoulder: an early knee keeps scattered cloud textured, a late one
# keeps the structure and colour of a large bright surface (glacier, desert).
ADAPTIVE_KNEE_RANGE = (0.65, 0.80)
# Rule c takes the adaptive shoulder only for a large bright area whose source
# nearly clips (a salt flat): its stretch top is at least this value.
CLIPPED_TOP = 240
_RULE_FIELDS = _V2_FIELDS | {"rule", "highlight_knee", "bright_share"}


def _shoulder_to_white(s, knee, top):
    """Linear up to ``knee``; above, an exponential shoulder that reaches 1 exactly at ``top``."""
    if s <= knee:
        return max(0.0, s)
    if top <= knee:
        return 1.0
    room = 1 - knee
    scale = 1 - math.exp(-(top - knee) / room)
    return min(1.0, knee + room * (1 - math.exp(-(s - knee) / room)) / scale)


def _valid_histogram(image):
    alpha = image.getchannel("A")
    return image.convert("L").histogram(mask=alpha.point(lambda value: 255 if value == 255 else 0))


def bright_share(histogram, low, high):
    """Share of the pixels in ``histogram`` brighter than BRIGHT_AREA_LEVEL after the stretch."""
    span = max(1, high - low)
    total = sum(histogram)
    if not total:
        return 0.0
    return sum(amount for value, amount in enumerate(histogram)
               if (value - low) / span > BRIGHT_AREA_LEVEL) / total


def _bright_weight(share):
    return min(1.0, max(0.0, (share - BRIGHT_AREA_RANGE[0]) / (BRIGHT_AREA_RANGE[1] - BRIGHT_AREA_RANGE[0])))


def _with_lut(image, lut):
    result = image.convert("RGB").point(lut * 3).convert("RGBA")
    result.putalpha(image.getchannel("A"))
    return result


def _white_shoulder_lut(low, high, knee, gamma):
    top = (255 - low) / (high - low)
    lut = []
    for value in range(256):
        x = WHITE_POINT * _shoulder_to_white((value - low) / (high - low), knee, top)
        lut.append(min(255, max(0, round((x ** gamma) * 255))))
    return lut


def _fixed_shoulder_lut(histogram, low, high, gamma):
    """adjust()'s stretch with its fading highlight roll-off, for another black point and gamma."""
    linear = [(value - low) / (high - low) for value in range(256)]
    total = sum(histogram)
    bright = sum(amount for value, amount in enumerate(histogram) if linear[value] > BRIGHT_LEVEL) / total
    roll_off = min(1.0, max(0.0, (BRIGHT_SHARE_RANGE[1] - bright) / (BRIGHT_SHARE_RANGE[1] - BRIGHT_SHARE_RANGE[0])))
    curve = [WHITE_POINT * ((1 - roll_off) * min(1.0, max(0.0, s)) + roll_off * _shoulder(s)) for s in linear]
    return [min(255, max(0, round((x ** gamma) * 255))) for x in curve]


def apply_rule(image, profile, rule, mosaic=True):
    """Apply tone rule ``rule`` (a-f); return (image, record).

    Mosaics get a manual brightness from the provider while no auto option is
    on; regular layers get it here. Saved values of an auto option stay in the
    profile and the record but have no effect while it is on.
    """
    blocks = TONE_RULES[rule]
    auto_brightness = bool(profile.get("auto_brightness", False))
    auto_contrast = bool(profile.get("auto_contrast", False))
    brightness = profile.get("brightness", 100)
    call = dict(profile)
    if auto_brightness and not blocks["brightness"]:
        # Rule a: auto brightness is on but neutral.
        call.update(auto_brightness=False, brightness=100)
    result, record = adjust(image, call)
    low, high, gamma = record["stretch_low"], record["stretch_high"], record["midtone_gamma"]
    knee, share = HIGHLIGHT_KNEE, 0.0
    if auto_contrast and (low, high) != (0, 255) and record["evaluated_pixels"]:
        histogram = _valid_histogram(image)
        share = bright_share(histogram, low, high)
        weight = _bright_weight(share)
        shoulder = blocks["shoulder"]
        if shoulder == "clipped":
            shoulder = "adaptive" if share >= BRIGHT_AREA_RANGE[1] and high >= CLIPPED_TOP else "fixed"
        black = low
        if blocks["bright_area"]:
            # A large bright area: the black point rises half as much and auto
            # brightness does not darken.
            black = round(low * (1 - 0.5 * weight))
            if auto_brightness and gamma > 1:
                gamma = gamma + weight * (1.0 - gamma)
        if shoulder == "adaptive":
            knee = ADAPTIVE_KNEE_RANGE[0] + weight * (ADAPTIVE_KNEE_RANGE[1] - ADAPTIVE_KNEE_RANGE[0])
            result = _with_lut(image, _white_shoulder_lut(black, high, knee, gamma))
        elif blocks["bright_area"]:
            result = _with_lut(image, _fixed_shoulder_lut(histogram, black, high, gamma))
        low = black
    elif not mosaic and not call["auto_brightness"] and not auto_contrast and brightness != 100:
        # A regular layer's manual brightness: the provider applies none.
        gamma = round(100 / brightness, 4)
        result = _with_lut(result, [min(255, max(0, round(((value / 255) ** gamma) * 255))) for value in range(256)])
    record.update(
        algorithm=RULE_ALGORITHM, rule=rule, auto_brightness=auto_brightness, brightness_percent=brightness,
        evalscript_brightness_percent=evalscript_brightness(profile, mosaic),
        stretch_low=low, midtone_gamma=round(gamma, 4),
        highlight_knee=round(knee, 4), bright_share=round(share, 4),
    )
    return result, record


def _validate_common(record, profile):
    for key, fallback in (("auto_brightness", False), ("auto_contrast", False),
                          ("contrast", 100), ("brightness", 100)):
        field = key if key.startswith("auto_") else key + "_percent"
        expected = profile.get(key, fallback)
        if type(record[field]) is not type(expected) or record[field] != expected:
            raise ValueError(f"Mosaic tone {field} conflicts with profile settings.")
    count = record["evaluated_pixels"]
    if type(count) is not int or not 0 <= count <= 1_000_000_000:
        raise ValueError("Invalid mosaic tone pixel count.")


def _validate_v1(record, profile):
    """Pictures made before mosaic-tone-v2 keep importing."""
    expected = 100 if profile.get("auto_brightness", False) else profile.get("brightness", 100)
    if type(record["evalscript_brightness_percent"]) is not int or record["evalscript_brightness_percent"] != expected:
        raise ValueError("Inconsistent evalscript brightness.")
    for field, low, high in (("brightness_gain", 0.5, 2), ("contrast_factor", 0, 3), ("contrast_offset", -1530, 128)):
        value = record[field]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Invalid mosaic tone {field}.")
    if not record["auto_brightness"] and record["brightness_gain"] != 1:
        raise ValueError("Unexpected automatic brightness gain.")
    if not record["auto_contrast"]:
        factor = profile.get("contrast", 100) / 100
        if record["contrast_factor"] != factor or record["contrast_offset"] != 128 * (1 - factor):
            raise ValueError("Inconsistent manual contrast parameters.")


def _validate_v2(record, profile):
    if (type(record["evalscript_brightness_percent"]) is not int
            or record["evalscript_brightness_percent"] != evalscript_brightness(profile)):
        raise ValueError("Inconsistent evalscript brightness.")
    low, high = record["stretch_low"], record["stretch_high"]
    if type(low) is not int or type(high) is not int or not 0 <= low < high <= 255:
        raise ValueError("Invalid mosaic tone stretch.")
    if not record["auto_contrast"] and (low, high) != (0, 255):
        raise ValueError("Unexpected automatic contrast stretch.")
    for field, minimum, maximum in (("contrast_pivot", *PIVOT_RANGE), ("midtone_gamma", 0.5, 4.0)):
        value = record[field]
        if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"Invalid mosaic tone {field}.")
    if not record["auto_brightness"]:
        brightness = profile.get("brightness", 100)
        expected = round(100 / brightness, 4) if record["auto_contrast"] and brightness != 100 else 1.0
        if record["midtone_gamma"] != expected:
            raise ValueError("Inconsistent manual brightness parameters.")


def _validate_rules(record, profile):
    if record["rule"] not in TONE_RULES:
        raise ValueError("Unknown tone rule.")
    brightness = profile.get("brightness", 100)
    if (type(record["evalscript_brightness_percent"]) is not int
            or record["evalscript_brightness_percent"] not in {100, brightness}):
        raise ValueError("Inconsistent evalscript brightness.")
    low, high = record["stretch_low"], record["stretch_high"]
    if type(low) is not int or type(high) is not int or not 0 <= low < high <= 255:
        raise ValueError("Invalid tone stretch.")
    if not record["auto_contrast"] and (low, high) != (0, 255):
        raise ValueError("Unexpected automatic contrast stretch.")
    for field, minimum, maximum in (("contrast_pivot", *PIVOT_RANGE), ("midtone_gamma", 0.5, 4.0),
                                    ("highlight_knee", 0.5, 0.95), ("bright_share", 0.0, 1.0)):
        value = record[field]
        if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"Invalid tone {field}.")
    if record["auto_brightness"] and not TONE_RULES[record["rule"]]["brightness"]:
        if record["midtone_gamma"] != 1.0:
            raise ValueError("Rule a keeps auto brightness neutral.")
    elif not record["auto_brightness"]:
        expected = (round(100 / brightness, 4)
                    if record["evalscript_brightness_percent"] == 100 and brightness != 100 else 1.0)
        if record["midtone_gamma"] != expected:
            raise ValueError("Inconsistent manual brightness parameters.")


def validate(record, profile):
    if not isinstance(record, dict):
        raise ValueError("Incomplete mosaic tone metadata.")
    fields = {"mosaic-tone-v1": _V1_FIELDS, ALGORITHM: _V2_FIELDS,
              RULE_ALGORITHM: _RULE_FIELDS}.get(record.get("algorithm"))
    if fields is None or record.get("scope") != SCOPE:
        raise ValueError("Unknown mosaic tone algorithm or scope.")
    if set(record) != fields:
        raise ValueError("Incomplete mosaic tone metadata.")
    _validate_common(record, profile)
    {"mosaic-tone-v1": _validate_v1, ALGORITHM: _validate_v2,
     RULE_ALGORITHM: _validate_rules}[record["algorithm"]](record, profile)
