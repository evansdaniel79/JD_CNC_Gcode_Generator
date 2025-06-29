import inkex
import math

class SVGParser:
    def __init__(self, svg_document):
        self.svg = svg_document
        self.scale = self.svg.unittouu('1mm')  # Conversion factor to mm

    def get_paths_by_color(self):
        black_paths = []
        red_paths = []

        for element in self.svg.selection.get():
            if isinstance(element, inkex.PathElement):
                style = inkex.Style.parse_str(element.get('style'))
                stroke_color = style.get('stroke', '#000000')
                
                if not stroke_color or stroke_color == 'none':
                    continue

                color = tuple(inkex.Color(stroke_color))

                # Check for black
                if all(c < 20 for c in color):
                    path_data = self._extract_path_data(element)
                    if path_data:
                        black_paths.append(path_data)
                # Check for red
                elif color[0] > 200 and color[1] < 50 and color[2] < 50:
                    path_data = self._extract_path_data(element)
                    if path_data:
                        red_paths.append(path_data)

        return black_paths, red_paths

    def _flatten_cubic_bezier(self, p0, p1, p2, p3, flatness=0.1):  # Reduced flatness for more points
        # Recursively subdivide cubic Bezier until flat
        def dist2(a, b):
            return (a[0]-b[0])**2 + (a[1]-b[1])**2
        
        def point_line_dist2(p, a, b):
            if a == b:
                return dist2(p, a)
            t = ((p[0]-a[0])*(b[0]-a[0]) + (p[1]-a[1])*(b[1]-a[1])) / dist2(b, a)
            t = max(0, min(1, t))
            proj = (a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1]))
            return dist2(p, proj)

        def recursive(p0, p1, p2, p3):
            d1 = point_line_dist2(p1, p0, p3)
            d2 = point_line_dist2(p2, p0, p3)
            if max(d1, d2) < flatness**2:
                return [p0, p3]
            # Subdivide
            p01 = ((p0[0]+p1[0])/2, (p0[1]+p1[1])/2)
            p12 = ((p1[0]+p2[0])/2, (p1[1]+p2[1])/2)
            p23 = ((p2[0]+p3[0])/2, (p2[1]+p3[1])/2)
            p012 = ((p01[0]+p12[0])/2, (p01[1]+p12[1])/2)
            p123 = ((p12[0]+p23[0])/2, (p12[1]+p23[1])/2)
            p0123 = ((p012[0]+p123[0])/2, (p012[1]+p123[1])/2)
            left = recursive(p0, p01, p012, p0123)
            right = recursive(p0123, p123, p23, p3)
            return left[:-1] + right
        
        return recursive(p0, p1, p2, p3)

    def _flatten_quadratic_bezier(self, p0, p1, p2, flatness=0.1):
        c1 = (p0[0] + 2/3*(p1[0]-p0[0]), p0[1] + 2/3*(p1[1]-p0[1]))
        c2 = (p2[0] + 2/3*(p1[0]-p2[0]), p2[1] + 2/3*(p1[1]-p2[1]))
        return self._flatten_cubic_bezier(p0, c1, c2, p2, flatness)

    def _extract_path_data(self, element, flatness=0.1):
        # Apply transformations from SVG
        transform = element.composed_transform()
        if transform is None:
            transform = inkex.Transform()
        
        path = element.path.to_absolute().transform(transform)
        arr = path.to_arrays()
        
        subpaths = []
        current = []
        last = (0, 0)
        last_ctrl = None
        
        for cmd, params in arr:
            if cmd == 'M':
                if current:
                    subpaths.append(current)
                x, y = params[0] / self.scale, params[1] / self.scale
                last = (x, y)
                current = [last]
                last_ctrl = None
            elif cmd == 'L':
                x, y = params[0] / self.scale, params[1] / self.scale
                pt = (x, y)
                current.append(pt)
                last = pt
                last_ctrl = None
            elif cmd == 'H':
                x = params[0] / self.scale
                pt = (x, last[1])
                current.append(pt)
                last = pt
                last_ctrl = None
            elif cmd == 'V':
                y = params[0] / self.scale
                pt = (last[0], y)
                current.append(pt)
                last = pt
                last_ctrl = None
            elif cmd == 'C':
                x1, y1 = params[0] / self.scale, params[1] / self.scale
                x2, y2 = params[2] / self.scale, params[3] / self.scale
                x3, y3 = params[4] / self.scale, params[5] / self.scale
                p0 = last
                p1 = (x1, y1)
                p2 = (x2, y2)
                p3 = (x3, y3)
                pts = self._flatten_cubic_bezier(p0, p1, p2, p3, flatness)
                current.extend(pts[1:])  # Skip first point (already in path)
                last = p3
                last_ctrl = p2
            elif cmd == 'S':
                x2, y2 = params[0] / self.scale, params[1] / self.scale
                x3, y3 = params[2] / self.scale, params[3] / self.scale
                p0 = last
                if last_ctrl:
                    rx = 2 * last[0] - last_ctrl[0]
                    ry = 2 * last[1] - last_ctrl[1]
                    p1 = (rx, ry)
                else:
                    p1 = p0
                p2 = (x2, y2)
                p3 = (x3, y3)
                pts = self._flatten_cubic_bezier(p0, p1, p2, p3, flatness)
                current.extend(pts[1:])
                last = p3
                last_ctrl = p2
            elif cmd == 'Q':
                x1, y1 = params[0] / self.scale, params[1] / self.scale
                x2, y2 = params[2] / self.scale, params[3] / self.scale
                p0 = last
                p1 = (x1, y1)
                p2 = (x2, y2)
                pts = self._flatten_quadratic_bezier(p0, p1, p2, flatness)
                current.extend(pts[1:])
                last = p2
                last_ctrl = p1
            elif cmd == 'T':
                x2, y2 = params[0] / self.scale, params[1] / self.scale
                p0 = last
                if last_ctrl:
                    rx = 2 * last[0] - last_ctrl[0]
                    ry = 2 * last[1] - last_ctrl[1]
                    p1 = (rx, ry)
                else:
                    p1 = p0
                p2 = (x2, y2)
                pts = self._flatten_quadratic_bezier(p0, p1, p2, flatness)
                current.extend(pts[1:])
                last = p2
                last_ctrl = p1
            elif cmd == 'A':
                # Better arc approximation
                x, y = params[5] / self.scale, params[6] / self.scale
                # Add intermediate points instead of just endpoint
                steps = 5
                for i in range(1, steps + 1):
                    t = i / steps
                    inter_x = last[0] * (1 - t) + x * t
                    inter_y = last[1] * (1 - t) + y * t
                    current.append((inter_x, inter_y))
                last = (x, y)
                last_ctrl = None
            elif cmd == 'Z':
                if current:
                    current.append(current[0])
        
        if current:
            subpaths.append(current)
        return subpaths