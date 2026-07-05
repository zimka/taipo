# encoding: utf-8

from tools.formatting import fmt_num

# encoding: utf-8
class _PyPoint:
    """Fallback point type when Foundation is unavailable (unit tests outside Glyphs)."""

    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y

def point(x, y):
    """Build a point value that ``GSNode.position`` accepts."""
    try:
        from Foundation import NSMakePoint

        return NSMakePoint(x, y)
    except Exception:
        return _PyPoint(x, y)

def get_component_transform(comp):
    """Return (m11, m12, m21, m22, tX, tY) from a GSComponent transform.

    Tries attribute access (NSAffineTransformStruct) then iteration fallback.
    Returns identity on failure.
    """
    try:
        t = comp.transform
        try:
            return (float(t.m11), float(t.m12), float(t.m21), float(t.m22),
                    float(t.tX), float(t.tY))
        except AttributeError:
            vals = tuple(t)
            return tuple(float(v) for v in vals[:6])
    except Exception:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

def apply_transform(m11, m12, m21, m22, tx, ty, x, y):
    """Apply a 2D affine transform to point (x, y)."""
    return m11 * x + m21 * y + tx, m12 * x + m22 * y + ty

def describe_transform(m11, m12, m21, m22, tx, ty):
    """Return a short human-readable description of a 2D affine transform."""
    eps = 1e-4
    pure_tx = abs(tx) > eps or abs(ty) > eps
    offset = " offset=(%s, %s)" % (fmt_num(tx), fmt_num(ty)) if pure_tx else ""
    if abs(m11 - 1) < eps and abs(m12) < eps and abs(m21) < eps and abs(m22 - 1) < eps:
        return ("offset=(%s, %s)" % (fmt_num(tx), fmt_num(ty))) if pure_tx else "identity"
    if abs(m11 + 1) < eps and abs(m12) < eps and abs(m21) < eps and abs(m22 - 1) < eps:
        return "mirror_x" + offset
    if abs(m11 - 1) < eps and abs(m12) < eps and abs(m21) < eps and abs(m22 + 1) < eps:
        return "mirror_y" + offset
    if abs(m11 + 1) < eps and abs(m12) < eps and abs(m21) < eps and abs(m22 + 1) < eps:
        return "rotate_180" + offset
    return "matrix=(%s,%s,%s,%s,%s,%s)" % (
        fmt_num(m11), fmt_num(m12), fmt_num(m21), fmt_num(m22),
        fmt_num(tx), fmt_num(ty),
    )
