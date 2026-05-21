import inkex
import math


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _parse_css_color(color_str):
    """
    Parse a CSS color string into an (r, g, b) tuple with 0-255 values.
    Handles: #rrggbb, #rgb, rgb(r,g,b), named colors (black, white, red, …).
    Returns None if the string is missing, 'none', or unrecognisable.
    """
    if not color_str:
        return None
    color_str = color_str.strip()
    if color_str.lower() == 'none':
        return None

    # Named colors (extend as needed)
    _NAMED = {
        'black':   (0,   0,   0),
        'white':   (255, 255, 255),
        'red':     (255, 0,   0),
        'green':   (0,   128, 0),
        'lime':    (0,   255, 0),
        'blue':    (0,   0,   255),
        'yellow':  (255, 255, 0),
        'cyan':    (0,   255, 255),
        'magenta': (255, 0,   255),
    }
    if color_str.lower() in _NAMED:
        return _NAMED[color_str.lower()]

    # #rrggbb
    if color_str.startswith('#'):
        h = color_str[1:]
        try:
            if len(h) == 6:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            if len(h) == 3:
                return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16))
        except ValueError:
            pass

    # rgb(r, g, b)
    if color_str.lower().startswith('rgb(') and color_str.endswith(')'):
        try:
            parts = color_str[4:-1].split(',')
            return tuple(int(p.strip()) for p in parts)
        except ValueError:
            pass

    return None


def _is_black(r, g, b):
    """True for black and near-black / dark-grey strokes."""
    # High brightness threshold but low saturation — max channel < 50,
    # and the spread between channels < 20 (so it's achromatic / grey).
    return max(r, g, b) < 50 and (max(r, g, b) - min(r, g, b)) < 20


def _is_red(r, g, b):
    """True for red and near-red strokes."""
    # Red channel dominant (>= 170), green and blue low, red clearly dominant.
    return r >= 170 and g < 80 and b < 80 and r > max(g, b) * 2


def _get_stroke_color(element):
    """
    Return the stroke (r, g, b) for an inkex element, or None if there is no
    visible stroke.  Checks the 'style' attribute first, then falls back to the
    'stroke' attribute directly on the element (common in simplified/exported SVGs).
    """
    # 1. Try the style attribute dict
    style = inkex.Style.parse_str(element.get('style', ''))
    stroke_val = style.get('stroke')

    # 2. Fall back to a direct stroke attribute
    if not stroke_val:
        stroke_val = element.get('stroke')

    return _parse_css_color(stroke_val)


# ---------------------------------------------------------------------------
# Arc tessellation
# ---------------------------------------------------------------------------

def _arc_to_points(x1, y1, rx, ry, phi_deg, large_arc, sweep, x2, y2,
                   flatness=0.1):
    """
    Convert a single SVG elliptical-arc segment to a list of (x, y) points.

    Implements the W3C SVG spec endpoint-to-centre parameterisation:
    https://www.w3.org/TR/SVG/implnote.html#ArcImplementationNotes

    Parameters
    ----------
    x1, y1       : start point (already the current point in the path)
    rx, ry       : ellipse radii (will be made absolute and scaled if needed)
    phi_deg      : x-axis rotation in degrees
    large_arc    : large-arc-flag  (0 or 1)
    sweep        : sweep-flag      (0 or 1)
    x2, y2       : end point
    flatness     : maximum allowed deviation from a true ellipse, in the same
                   units as the coordinates.  Smaller = more points.

    Returns
    -------
    List of (x, y) tuples NOT including (x1, y1), ending exactly at (x2, y2).
    Returns [] for degenerate arcs (identical endpoints, zero radius).
    """
    # --- Degenerate cases ---
    if x1 == x2 and y1 == y2:
        return []
    if rx == 0.0 or ry == 0.0:
        # Treat as a straight line
        return [(x2, y2)]

    rx = abs(rx)
    ry = abs(ry)
    phi = math.radians(phi_deg)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    # --- Step 1: transform endpoint to the ellipse's coordinate frame ---
    dx = (x1 - x2) / 2.0
    dy = (y1 - y2) / 2.0
    x1p =  cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    # --- Step 2: scale radii if they are too small (W3C F.6.6) ---
    lam = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s

    # --- Step 3: compute centre in the ellipse frame ---
    rx2, ry2 = rx * rx, ry * ry
    x1p2, y1p2 = x1p * x1p, y1p * y1p

    num = max(0.0, rx2 * ry2 - rx2 * y1p2 - ry2 * x1p2)
    den = rx2 * y1p2 + ry2 * x1p2
    sq = math.sqrt(num / den) if den != 0.0 else 0.0

    # Sign of sq depends on the flags
    if large_arc == sweep:
        sq = -sq

    cxp =  sq * rx * y1p / ry
    cyp = -sq * ry * x1p / rx

    # --- Step 4: transform centre back to original coordinate system ---
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    # --- Step 5: compute start angle and angular span ---
    def _angle(ux, uy, vx, vy):
        """Signed angle from vector (ux,uy) to vector (vx,vy)."""
        n = math.sqrt(ux * ux + uy * uy) * math.sqrt(vx * vx + vy * vy)
        if n == 0.0:
            return 0.0
        c = max(-1.0, min(1.0, (ux * vx + uy * vy) / n))
        a = math.acos(c)
        if ux * vy - uy * vx < 0.0:
            a = -a
        return a

    theta1 = _angle(1.0, 0.0,
                    (x1p - cxp) / rx,
                    (y1p - cyp) / ry)

    dtheta = _angle((x1p - cxp) / rx,  (y1p - cyp) / ry,
                    (-x1p - cxp) / rx, (-y1p - cyp) / ry)

    # Adjust dtheta for the sweep direction
    if not sweep and dtheta > 0.0:
        dtheta -= 2.0 * math.pi
    elif sweep and dtheta < 0.0:
        dtheta += 2.0 * math.pi

    # --- Step 6: tessellate the arc into line segments ---
    # Choose n so that the maximum chord deviation is <= flatness.
    # For a circle of radius r: deviation = r * (1 - cos(dtheta / 2n))
    # Solving for n: n >= dtheta / (2 * acos(1 - flatness/r))
    r_approx = math.sqrt(rx * ry)  # geometric mean of radii
    if r_approx > 0.0 and flatness > 0.0:
        arg = 1.0 - flatness / r_approx
        arg = max(-1.0, min(1.0, arg))          # clamp for acos safety
        n = int(math.ceil(abs(dtheta) / (2.0 * math.acos(arg))))
        n = max(4, min(n, 256))                 # at least 4, cap at 256
    else:
        n = 16

    pts = []
    for i in range(1, n + 1):
        t = i / n
        angle_t = theta1 + t * dtheta
        # Point on the ellipse in the rotated frame
        xp = rx * math.cos(angle_t)
        yp = ry * math.sin(angle_t)
        # Rotate back to the original frame and translate
        x = cos_phi * xp - sin_phi * yp + cx
        y = sin_phi * xp + cos_phi * yp + cy
        pts.append((x, y))

    # Force the very last point to the exact endpoint so floating-point
    # drift cannot cause a gap at the next command.
    pts[-1] = (x2, y2)
    return pts


# ---------------------------------------------------------------------------
# Main parser class
# ---------------------------------------------------------------------------

class SVGParser:
    """
    Parses selected Inkscape elements into lists of (x, y) polyline subpaths,
    converting all coordinates to millimetres.

    Black-stroked paths  → cut paths
    Red-stroked paths    → score paths

    All SVG curve types (C, S, Q, T, A) are tessellated into straight-line
    segments whose chord deviation stays within `flatness` mm.
    """

    # Default chord-error tolerance for curve tessellation (mm)
    DEFAULT_FLATNESS = 0.1

    # Stitch tolerance: subpaths whose endpoints are within this distance
    # (mm) will be joined into a single continuous path.
    STITCH_TOLERANCE = 0.5

    def __init__(self, svg_document):
        self.svg = svg_document
        # inkex.unittouu('1mm') gives the number of SVG user units per mm,
        # so dividing coordinates by this converts them to mm.
        self.scale = self.svg.unittouu('1mm')

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_paths_by_color(self):
        """
        Walk the current Inkscape selection and separate paths into cut
        (black) and score (red) groups.

        Returns
        -------
        cut_paths, score_paths
            Each is a list containing one list of subpaths:
              [ [ [(x,y), …], [(x,y), …], … ] ]
            This outer list wrapping matches what GCodeLogic expects.
        """
        black_subpaths = []
        red_subpaths   = []

        for element in self.svg.selection.get():
            if not isinstance(element, inkex.PathElement):
                continue

            rgb = _get_stroke_color(element)
            if rgb is None:
                continue

            r, g, b = rgb
            if _is_black(r, g, b):
                subpaths = self._extract_subpaths(element)
                black_subpaths.extend(subpaths)
            elif _is_red(r, g, b):
                subpaths = self._extract_subpaths(element)
                red_subpaths.extend(subpaths)
            # Any other colour is ignored (not cut, not score)

        # Sort for deterministic ordering before stitching
        black_subpaths.sort(key=lambda s: (s[0][0], s[0][1]) if s else (0.0, 0.0))
        red_subpaths.sort(  key=lambda s: (s[0][0], s[0][1]) if s else (0.0, 0.0))

        stitched_black = self._stitch_subpaths(black_subpaths, self.STITCH_TOLERANCE)
        stitched_red   = self._stitch_subpaths(red_subpaths,   self.STITCH_TOLERANCE)

        # Wrap in an outer list to match GCodeLogic's expected structure
        return [stitched_black], [stitched_red]

    # ------------------------------------------------------------------
    # Stitching
    # ------------------------------------------------------------------

    @staticmethod
    def _stitch_subpaths(subpaths, tolerance):
        """
        Greedily chain subpaths into longer continuous paths by matching
        endpoints within `tolerance` mm.  Reverses a subpath if that gives
        a closer join.  Subpaths that cannot be chained start a new chain.

        The result is a list of stitched subpaths (each a list of (x,y) pts).
        """
        if not subpaths:
            return []

        # Work on a copy; sort longest-first so large shapes anchor chains
        unused = sorted(subpaths, key=lambda s: -len(s))
        stitched = []

        while unused:
            chain = list(unused.pop(0))

            extended = True
            while extended and unused:
                extended = False
                tail = chain[-1]

                best_idx  = None
                best_dist = None
                best_rev  = False

                for idx, sub in enumerate(unused):
                    d_start = math.hypot(sub[0][0]  - tail[0], sub[0][1]  - tail[1])
                    d_end   = math.hypot(sub[-1][0] - tail[0], sub[-1][1] - tail[1])

                    if best_dist is None or d_start < best_dist:
                        best_dist = d_start
                        best_idx  = idx
                        best_rev  = False

                    if d_end < best_dist:
                        best_dist = d_end
                        best_idx  = idx
                        best_rev  = True

                if best_dist is not None and best_dist <= tolerance:
                    next_sub = list(unused.pop(best_idx))
                    if best_rev:
                        next_sub = list(reversed(next_sub))

                    # Skip duplicate junction point
                    if math.hypot(next_sub[0][0] - chain[-1][0],
                                  next_sub[0][1] - chain[-1][1]) < 1e-6:
                        chain.extend(next_sub[1:])
                    else:
                        chain.extend(next_sub)

                    extended = True  # keep trying to extend this chain

            stitched.append(chain)

        return stitched

    # ------------------------------------------------------------------
    # Path extraction
    # ------------------------------------------------------------------

    def _extract_subpaths(self, element, flatness=None):
        """
        Extract all subpaths from an inkex.PathElement.

        Applies the element's composed transform, converts all coordinates to
        mm, and tessellates every curve type into straight-line segments.

        Returns a list of subpaths, each a list of (x, y) tuples.
        """
        if flatness is None:
            flatness = self.DEFAULT_FLATNESS

        # Apply all ancestor transforms so we work in the SVG root frame
        transform = element.composed_transform()
        if transform is None:
            transform = inkex.Transform()

        path = element.path.to_absolute().transform(transform)
        arr  = path.to_arrays()

        subpaths    = []
        current     = []        # points in the current subpath
        last        = (0.0, 0.0)
        start       = (0.0, 0.0)  # for Z close-path
        last_ctrl   = None      # last Bezier control point (for S and T commands)
        last_cmd    = None      # previous command letter

        def to_mm(v):
            return v / self.scale

        for cmd, params in arr:
            if cmd == 'M':
                # MoveTo — start a new subpath
                if current:
                    subpaths.append(current)
                x, y = to_mm(params[0]), to_mm(params[1])
                last  = (x, y)
                start = (x, y)
                current   = [last]
                last_ctrl = None

            elif cmd == 'L':
                x, y = to_mm(params[0]), to_mm(params[1])
                pt = (x, y)
                current.append(pt)
                last      = pt
                last_ctrl = None

            elif cmd == 'H':
                # Horizontal line
                x = to_mm(params[0])
                pt = (x, last[1])
                current.append(pt)
                last      = pt
                last_ctrl = None

            elif cmd == 'V':
                # Vertical line
                y = to_mm(params[0])
                pt = (last[0], y)
                current.append(pt)
                last      = pt
                last_ctrl = None

            elif cmd == 'C':
                # Cubic Bézier: C x1 y1  x2 y2  x y
                p1 = (to_mm(params[0]), to_mm(params[1]))
                p2 = (to_mm(params[2]), to_mm(params[3]))
                p3 = (to_mm(params[4]), to_mm(params[5]))
                pts = self._flatten_cubic(last, p1, p2, p3, flatness)
                current.extend(pts[1:])
                last      = p3
                last_ctrl = p2

            elif cmd == 'S':
                # Smooth cubic Bézier: S x2 y2  x y
                # First control point is reflection of the last C/S control point
                p2 = (to_mm(params[0]), to_mm(params[1]))
                p3 = (to_mm(params[2]), to_mm(params[3]))
                if last_cmd in ('C', 'S') and last_ctrl is not None:
                    p1 = (2.0 * last[0] - last_ctrl[0],
                          2.0 * last[1] - last_ctrl[1])
                else:
                    p1 = last  # degenerate: control point = current point
                pts = self._flatten_cubic(last, p1, p2, p3, flatness)
                current.extend(pts[1:])
                last      = p3
                last_ctrl = p2

            elif cmd == 'Q':
                # Quadratic Bézier: Q x1 y1  x y
                p1 = (to_mm(params[0]), to_mm(params[1]))
                p2 = (to_mm(params[2]), to_mm(params[3]))
                pts = self._flatten_quadratic(last, p1, p2, flatness)
                current.extend(pts[1:])
                last      = p2
                last_ctrl = p1

            elif cmd == 'T':
                # Smooth quadratic Bézier: T x y
                p2 = (to_mm(params[0]), to_mm(params[1]))
                if last_cmd in ('Q', 'T') and last_ctrl is not None:
                    p1 = (2.0 * last[0] - last_ctrl[0],
                          2.0 * last[1] - last_ctrl[1])
                else:
                    p1 = last
                pts = self._flatten_quadratic(last, p1, p2, flatness)
                current.extend(pts[1:])
                last      = p2
                last_ctrl = p1

            elif cmd == 'A':
                # Elliptical arc: A rx ry  x-rotation  large-arc-flag  sweep-flag  x y
                rx       = abs(to_mm(params[0]))
                ry       = abs(to_mm(params[1]))
                phi_deg  = float(params[2])
                large    = int(params[3])
                sweep    = int(params[4])
                x2       = to_mm(params[5])
                y2       = to_mm(params[6])

                pts = _arc_to_points(
                    last[0], last[1],
                    rx, ry,
                    phi_deg, large, sweep,
                    x2, y2,
                    flatness
                )
                current.extend(pts)
                last      = (x2, y2)
                last_ctrl = None

            elif cmd == 'Z':
                # ClosePath — add the start point to close the loop, then
                # save the subpath.  A new 'M' will be needed to continue.
                if current:
                    # Only append the closing point if it differs from the last
                    if math.hypot(current[-1][0] - start[0],
                                  current[-1][1] - start[1]) > 1e-6:
                        current.append(start)
                    subpaths.append(current)
                    current = []
                last      = start
                last_ctrl = None

            last_cmd = cmd

        # Catch any unterminated subpath (no trailing Z)
        if current:
            subpaths.append(current)

        return subpaths

    # ------------------------------------------------------------------
    # Curve tessellation helpers
    # ------------------------------------------------------------------

    def _flatten_cubic(self, p0, p1, p2, p3, flatness):
        """
        Recursively subdivide a cubic Bézier curve until each chord segment
        deviates from the true curve by no more than `flatness`.

        Uses the convex-hull property: if both interior control points are
        within flatness of the chord p0→p3, the curve is flat enough.
        """
        def _pt_line_dist2(p, a, b):
            """Squared distance from point p to line segment a→b."""
            dx, dy = b[0] - a[0], b[1] - a[1]
            if dx == 0.0 and dy == 0.0:
                return (p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2
            t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)
            t = max(0.0, min(1.0, t))
            px = a[0] + t * dx
            py = a[1] + t * dy
            return (p[0] - px) ** 2 + (p[1] - py) ** 2

        flatness2 = flatness * flatness

        def _subdivide(p0, p1, p2, p3):
            # Flatness test on both interior control points
            if (_pt_line_dist2(p1, p0, p3) <= flatness2 and
                    _pt_line_dist2(p2, p0, p3) <= flatness2):
                return [p0, p3]
            # De Casteljau subdivision at t=0.5
            p01   = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
            p12   = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
            p23   = ((p2[0] + p3[0]) / 2, (p2[1] + p3[1]) / 2)
            p012  = ((p01[0] + p12[0]) / 2, (p01[1] + p12[1]) / 2)
            p123  = ((p12[0] + p23[0]) / 2, (p12[1] + p23[1]) / 2)
            p0123 = ((p012[0] + p123[0]) / 2, (p012[1] + p123[1]) / 2)
            left  = _subdivide(p0, p01, p012, p0123)
            right = _subdivide(p0123, p123, p23, p3)
            return left[:-1] + right   # drop duplicate midpoint

        return _subdivide(p0, p1, p2, p3)

    def _flatten_quadratic(self, p0, p1, p2, flatness):
        """
        Flatten a quadratic Bézier by elevating it to cubic then delegating.
        Elevation: Q(p0,p1,p2) → C(p0, p0+2/3*(p1-p0), p2+2/3*(p1-p2), p2)
        """
        c1 = (p0[0] + 2.0 / 3.0 * (p1[0] - p0[0]),
              p0[1] + 2.0 / 3.0 * (p1[1] - p0[1]))
        c2 = (p2[0] + 2.0 / 3.0 * (p1[0] - p2[0]),
              p2[1] + 2.0 / 3.0 * (p1[1] - p2[1]))
        return self._flatten_cubic(p0, c1, c2, p2, flatness)