"""Load Blender-exported .bin assets (legacy .billboard names also work), one frame at a time."""

import math
import struct

_HEADER = '<4sBBBBHHffI'
_HEADER_SIZE = 24


def _read_exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError('Truncated billboard asset')
    return data


def _load(path):
    from picoware.engine.billboard import Billboard
    from picoware.engine.image import Image
    from picoware.system.vector import Vector

    with open(path, 'rb') as stream:
        magic, version, directions, poses, flags, width, height, fps, world_height, size = struct.unpack(
            _HEADER, _read_exact(stream, _HEADER_SIZE))
        if magic != b'PWBB' or version != 1:
            raise ValueError('Unsupported billboard asset format')
        if directions not in (1, 4, 8) or not 1 <= poses <= 8 or flags & ~1:
            raise ValueError('Invalid billboard counts or flags')
        if width not in (8, 16, 32, 64, 96, 128) or height != width:
            raise ValueError('Invalid billboard dimensions')
        if not math.isfinite(fps) or not 0 < fps <= 60 or not math.isfinite(world_height) or world_height <= 0:
            raise ValueError('Invalid billboard timing or world height')
        pixels_size = width * height
        mask_size = (pixels_size + 7) // 8
        if size != directions * poses * (pixels_size + mask_size):
            raise ValueError('Invalid billboard payload size')
        stream.seek(0, 2)
        if stream.tell() != _HEADER_SIZE + size:
            raise ValueError('Truncated billboard asset or trailing data')
        stream.seek(_HEADER_SIZE)
        asset = Billboard(directions, poses)
        set_data = getattr(asset, "set_direction_pose_data", None)
        for pose in range(poses):
            for direction in range(directions):
                pixels = _read_exact(stream, pixels_size)
                mask = _read_exact(stream, mask_size)
                if set_data is not None:
                    set_data(direction, pose, width, height, pixels, mask)
                else:
                    image = Image(Vector(width, height), True, pixels, None, mask)
                    asset.set_direction_pose(direction, pose, image)
                    del image
                del pixels, mask
    return asset, world_height, poses, fps, bool(flags & 1)


def load_billboard(path):
    """Return (asset, world_height) from a single .bin or legacy .billboard file."""
    asset, height, _, _, _ = _load(path)
    return asset, height


class BillboardAnimation:
    """Call update(dt_seconds) once per game update. No timer or background task."""
    def __init__(self, entity, poses, fps=8.0, loop=True):
        import math
        if not 1 <= poses <= 8 or not math.isfinite(fps) or not 0 < fps <= 60:
            raise ValueError('Invalid pose count or playback rate')
        self.entity = entity
        self.poses = poses
        self.fps = fps
        self.loop = loop
        self.elapsed = 0.0
        self.finished = False
        entity.billboard_pose = 0

    def reset(self):
        self.elapsed = 0.0
        self.finished = False
        self.entity.billboard_pose = 0

    def update(self, dt_seconds):
        import math
        if not math.isfinite(dt_seconds) or dt_seconds < 0:
            raise ValueError('Elapsed time must be finite and non-negative')
        if self.finished:
            return
        duration = self.poses / self.fps
        self.elapsed += dt_seconds
        if self.loop:
            self.elapsed %= duration
        elif self.elapsed >= duration:
            self.elapsed = duration
            self.finished = True
        self.entity.billboard_pose = min(self.poses-1, int(self.elapsed * self.fps))


def load_animation(path, entity, world_height=None):
    """Attach the asset and return (asset, animation). Call animation.update(dt)."""
    if world_height is not None and (not math.isfinite(world_height) or world_height <= 0):
        raise ValueError('World height must be positive and finite')
    asset, height, poses, fps, loop = _load(path)
    entity.set_billboard(asset, height if world_height is None else world_height)
    return asset, BillboardAnimation(entity, poses, fps, loop)
