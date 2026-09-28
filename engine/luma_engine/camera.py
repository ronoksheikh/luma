"""3D perspective camera, point projection and plane homographies.

Coordinate convention (After-Effects-like): world units are pixels, x right, y *down*,
z *into* the screen.  The default camera sits at z = -distance looking at +z with a
distance chosen so the z = 0 plane maps 1:1 onto the frame — flat 2D layers at z = 0 are
untouched, and moving things to z > 0 pushes them away.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import skia

from .brand import skia_matrix


def rot_x(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def rot_y(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rot_z(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def euler(rx: float = 0.0, ry: float = 0.0, rz: float = 0.0) -> np.ndarray:
    """Rotation matrix applying X, then Y, then Z (degrees)."""
    return rot_z(rz) @ rot_y(ry) @ rot_x(rx)


@dataclass
class Camera:
    width: int
    height: int
    fov_deg: float = 39.6  # ≈ 50 mm on full frame (vertical FOV)
    position: np.ndarray | None = None
    target: np.ndarray | None = None
    up: tuple = (0.0, -1.0, 0.0)  # y is down in screen space, so "up" is −y

    def __post_init__(self):
        self.focal = (self.height / 2) / math.tan(math.radians(self.fov_deg) / 2)
        cx, cy = self.width / 2, self.height / 2
        if self.position is None:
            self.position = np.array([cx, cy, -self.focal])
        if self.target is None:
            self.target = np.array([cx, cy, 0.0])
        self.position = np.asarray(self.position, dtype=np.float64)
        self.target = np.asarray(self.target, dtype=np.float64)

    @property
    def distance(self) -> float:
        return float(np.linalg.norm(self.target - self.position))

    def basis(self) -> np.ndarray:
        """Rows: camera right, down, forward (world)."""
        f = self.target - self.position
        f = f / np.linalg.norm(f)
        down = -np.asarray(self.up, dtype=np.float64)
        r = np.cross(down, f)  # right-handed with y down: right × down = forward
        if np.linalg.norm(r) < 1e-9:
            r = np.array([1.0, 0.0, 0.0])
        r = r / np.linalg.norm(r)
        d = np.cross(f, r)
        return np.stack([r, d, f])

    def to_camera(self, pts: np.ndarray) -> np.ndarray:
        return (np.atleast_2d(pts) - self.position) @ self.basis().T

    def project(self, pts) -> tuple[np.ndarray, np.ndarray]:
        """World points (N, 3) → pixel coords (N, 2) and depth (N,) along the view axis."""
        c = self.to_camera(np.asarray(pts, dtype=np.float64))
        z = c[:, 2]
        zz = np.where(np.abs(z) < 1e-9, 1e-9, z)
        x = self.focal * c[:, 0] / zz + self.width / 2
        y = self.focal * c[:, 1] / zz + self.height / 2
        return np.stack([x, y], axis=1), z

    def scale_at(self, z_world: float) -> float:
        """Apparent scale of a front-facing object at world depth z (1.0 at z = 0)."""
        depth = z_world - self.position[2]
        return self.focal / depth if depth > 0 else 0.0

    def plane_homography(self, origin, u_axis, v_axis) -> np.ndarray:
        """3×3 homography mapping layer coords (u, v) → pixels for the plane
        ``origin + u·u_axis + v·v_axis`` (vectors in world units per layer unit)."""
        o = np.asarray(origin, dtype=np.float64)
        u = np.asarray(u_axis, dtype=np.float64)
        v = np.asarray(v_axis, dtype=np.float64)
        B = self.basis()
        K = np.array([[self.focal, 0, self.width / 2], [0, self.focal, self.height / 2], [0, 0, 1]])
        cols = np.stack([B @ u, B @ v, B @ (o - self.position)], axis=1)
        H = K @ cols
        return H / H[2, 2] if abs(H[2, 2]) > 1e-12 else H

    def layer_matrix(self, layer_w: float, layer_h: float, center=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0),
                     scale: float = 1.0, anchor=(0.5, 0.5)) -> np.ndarray:
        """Homography for a flat layer of size (w, h) placed in 3D.

        ``center`` is the world position of the layer's anchor point, ``rotation`` Euler
        degrees (x, y, z).
        """
        R = euler(*rotation) * scale
        o = np.asarray(center, dtype=np.float64) - R @ np.array([anchor[0] * layer_w, anchor[1] * layer_h, 0.0])
        return self.plane_homography(o, R[:, 0], R[:, 1])

    def skia_matrix(self, H: np.ndarray) -> skia.Matrix:
        return skia_matrix(H)

    def is_front_facing(self, H: np.ndarray) -> bool:
        return float(np.linalg.det(H[:2, :2])) > 0


def apply_h(H: np.ndarray, pts) -> np.ndarray:
    p = np.atleast_2d(np.asarray(pts, dtype=np.float64))
    q = np.c_[p, np.ones(len(p))] @ H.T
    return q[:, :2] / q[:, 2:3]


__all__ = ["Camera", "apply_h", "euler", "lambert", "rot_x", "rot_y", "rot_z", "to_light_vector"]


# ======================================================================================
# lighting (sign conventions are explicit and unit-tested)
# ======================================================================================


def to_light_vector(light_pos, surface_pos) -> np.ndarray:
    """Unit vector pointing FROM the surface TOWARDS the light (the Lambert convention).

    Getting this sign wrong lights surfaces from behind: faces facing the key light go
    dark and back faces blow out.  Always build light vectors with this helper.
    """
    v = np.asarray(light_pos, dtype=np.float64) - np.asarray(surface_pos, dtype=np.float64)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-12)


def lambert(normal, to_light, wrap: float = 0.0) -> np.ndarray:
    """Diffuse term max(0, n·l) (optionally "wrapped" for soft terminators).

    ``normal`` points out of the visible side of the surface; with our camera (looking
    along +z) a surface facing the camera has normal (0, 0, −1).
    """
    n = np.asarray(normal, dtype=np.float64)
    n = n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-12)
    d = np.sum(n * np.asarray(to_light, dtype=np.float64), axis=-1)
    return np.clip((d + wrap) / (1 + wrap), 0.0, 1.0)
