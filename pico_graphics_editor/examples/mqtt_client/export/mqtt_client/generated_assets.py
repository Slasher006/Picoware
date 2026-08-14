# @picoware-generated structure=1
# @picoware-generated role=assets
# @picoware-generated project=project_13106341
# @picoware-generator version=1.1.0
# This file is editor-owned. Regenerate it instead of editing it manually.

_RESOURCE_NAME = 'generated_assets.pga'
_RESOURCE_SHA256 = '43c401620d0081250f9667f78382c56d2551b14702763f3f50d2f834306f62a3'
_PROJECT_ID = 'project_13106341'
_MAGIC = b"PGA3"
_MAX_TEXT = 4096
_INDEX_SIZE = 8
_FRAME_SIZE = 12
_WAV_SIZE = 26
_IMAGE = 1
_WAV = 2
_MAX_AUDIO_CHUNK = 4096


def _resource_path():
    module_file = globals().get("__file__", "")
    base = module_file.rsplit("/", 1)[0] if "/" in module_file else ""
    return (base + "/" if base else "") + _RESOURCE_NAME


def _read_exact(handle, size):
    data = handle.read(size)
    return data if data is not None and len(data) == size else None


def _u16(data, offset=0):
    return data[offset] | (data[offset + 1] << 8)


def _u32(data, offset=0):
    return (data[offset] | (data[offset + 1] << 8) |
            (data[offset + 2] << 16) | (data[offset + 3] << 24))


def _i32(data, offset=0):
    value = _u32(data, offset)
    return value - 0x100000000 if value & 0x80000000 else value


def _asset_hash(asset_id):
    value = 0x811C9DC5
    for byte in asset_id:
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


def _open_resource():
    try:
        handle = open(_resource_path(), "rb")
    except OSError:
        return None
    try:
        if _read_exact(handle, 4) != _MAGIC:
            raise ValueError
        raw = _read_exact(handle, 2)
        if raw is None:
            raise ValueError
        project_size = _u16(raw)
        if project_size < 1 or project_size > _MAX_TEXT:
            raise ValueError
        project = _read_exact(handle, project_size)
        if project is None or project.decode("utf-8") != _PROJECT_ID:
            raise ValueError
        raw = _read_exact(handle, 10)
        if raw is None:
            raise ValueError
        count = _u16(raw)
        payload_start = _u32(raw, 2)
        total_size = _u32(raw, 6)
        index_start = handle.tell()
        index_end = index_start + count * _INDEX_SIZE
        if index_end > payload_start or payload_start > total_size:
            raise ValueError
        return (handle, count, index_start, index_end, payload_start, total_size)
    except Exception:
        handle.close()
        return None


def _index_entry(handle, index_start, index, index_end):
    offset = index_start + index * _INDEX_SIZE
    if offset < index_start or offset + _INDEX_SIZE > index_end:
        return None
    handle.seek(offset)
    raw = _read_exact(handle, _INDEX_SIZE)
    return None if raw is None else (_u32(raw), _u32(raw, 4))


def _record(opened, record_offset, wanted_id):
    handle, unused_count, unused_start, index_end, payload_start, total = opened
    if record_offset < index_end or record_offset >= payload_start:
        return None
    handle.seek(record_offset)
    common = _read_exact(handle, 4)
    if common is None or common[1] != 0:
        return None
    kind = common[0]
    asset_id_size = _u16(common, 2)
    if (kind != _IMAGE and kind != _WAV) or not 1 <= asset_id_size <= _MAX_TEXT:
        return None
    asset_id = _read_exact(handle, asset_id_size)
    if asset_id is None or asset_id != wanted_id:
        return None
    raw = _read_exact(handle, 2)
    if raw is None:
        return None
    name_size = _u16(raw)
    if name_size > _MAX_TEXT or handle.tell() + name_size > payload_start:
        return None
    handle.seek(handle.tell() + name_size)
    if kind == _IMAGE:
        raw = _read_exact(handle, 14)
        if raw is None:
            return None
        width = _u16(raw)
        height = _u16(raw, 2)
        frames = _u16(raw, 12)
        frame_records = handle.tell()
        if (width < 1 or width > 320 or height < 1 or height > 320 or
                frames < 1 or frame_records + frames * _FRAME_SIZE > payload_start):
            return None
        return (_IMAGE, width, height, _i32(raw, 4), _i32(raw, 8),
                frames, frame_records)
    raw = _read_exact(handle, _WAV_SIZE)
    if raw is None:
        return None
    channels = raw[0]
    bits = raw[1]
    rate = _u32(raw, 2)
    duration = _u32(raw, 6)
    loop_start = _u32(raw, 10)
    loop_end = _u32(raw, 14)
    offset = _u32(raw, 18)
    length = _u32(raw, 22)
    if (channels < 1 or channels > 2 or bits not in (8, 16, 24) or rate < 1 or
            length < 12 or offset < payload_start or offset + length > total):
        return None
    if (loop_start == 0xFFFFFFFF) != (loop_end == 0xFFFFFFFF):
        return None
    if loop_start != 0xFFFFFFFF and not 0 <= loop_start < loop_end <= duration:
        return None
    return (_WAV, channels, bits, rate, duration, loop_start, loop_end,
            offset, length)


def _find_resource(opened, asset_id):
    try:
        wanted_id = asset_id.encode("utf-8")
    except Exception:
        return None
    if not wanted_id or len(wanted_id) > _MAX_TEXT:
        return None
    handle, count, index_start, index_end, unused_payload, unused_total = opened
    wanted_hash = _asset_hash(wanted_id)
    low = 0
    high = count
    while low < high:
        middle = (low + high) // 2
        entry = _index_entry(handle, index_start, middle, index_end)
        if entry is None:
            return None
        if entry[0] < wanted_hash:
            low = middle + 1
        else:
            high = middle
    while low < count:
        entry = _index_entry(handle, index_start, low, index_end)
        if entry is None or entry[0] != wanted_hash:
            return None
        resource = _record(opened, entry[1], wanted_id)
        if resource is not None:
            return resource
        low += 1
    return None


def _metadata(asset_id, kind):
    opened = _open_resource()
    if opened is None:
        return None
    try:
        resource = _find_resource(opened, asset_id)
        return resource if resource is not None and resource[0] == kind else None
    finally:
        opened[0].close()


def has_asset(asset_id):
    return _metadata(asset_id, _IMAGE) is not None


def asset_size(asset_id):
    asset = _metadata(asset_id, _IMAGE)
    return None if asset is None else (asset[1], asset[2])


def frame_count(asset_id):
    asset = _metadata(asset_id, _IMAGE)
    return 0 if asset is None else asset[5]


def has_wav(asset_id):
    return _metadata(asset_id, _WAV) is not None


def wav_info(asset_id):
    wav = _metadata(asset_id, _WAV)
    if wav is None:
        return None
    loop_start = None if wav[5] == 0xFFFFFFFF else wav[5]
    loop_end = None if wav[6] == 0xFFFFFFFF else wav[6]
    return (wav[3], wav[1], wav[2], wav[4], loop_start, loop_end, wav[8])


def wav_path(asset_id):
    return None


def read_wav_chunk(asset_id, offset=0, size=1024):
    opened = _open_resource()
    if opened is None:
        return None
    handle = opened[0]
    try:
        wav = _find_resource(opened, asset_id)
        if wav is None or wav[0] != _WAV:
            return None
        try:
            offset = max(0, int(offset))
            size = max(0, min(_MAX_AUDIO_CHUNK, int(size)))
        except (TypeError, ValueError):
            return None
        if offset >= wav[8] or size == 0:
            return b""
        size = min(size, wav[8] - offset)
        handle.seek(wav[7] + offset)
        return _read_exact(handle, size)
    finally:
        handle.close()


def extract_wav(asset_id, destination):
    opened = _open_resource()
    if opened is None or not isinstance(destination, str) or not destination:
        return False
    handle = opened[0]
    target = None
    temporary = destination + ".pga-tmp"
    import os
    try:
        wav = _find_resource(opened, asset_id)
        if wav is None or wav[0] != _WAV:
            return False
        target = open(temporary, "wb")
        handle.seek(wav[7])
        remaining = wav[8]
        while remaining:
            chunk = _read_exact(handle, min(1024, remaining))
            if chunk is None or target.write(chunk) != len(chunk):
                return False
            remaining -= len(chunk)
        target.close()
        target = None
        try:
            os.remove(destination)
        except OSError:
            pass
        os.rename(temporary, destination)
        return True
    except (OSError, ValueError):
        return False
    finally:
        if target is not None:
            target.close()
        handle.close()
        try:
            os.remove(temporary)
        except OSError:
            pass


def draw_asset(draw, asset_id, x, y, frame=0, scale=1):
    opened = _open_resource()
    if opened is None:
        return False
    handle = opened[0]
    try:
        asset = _find_resource(opened, asset_id)
        if asset is None or asset[0] != _IMAGE:
            return False
        try:
            frame = int(frame)
        except (TypeError, ValueError):
            frame = 0
        if frame < 0 or frame >= asset[5]:
            frame = 0
        try:
            scale = max(1, int(scale))
        except (TypeError, ValueError):
            scale = 1
        width = asset[1]
        height = asset[2]
        mask_size = (width + 7) // 8
        row_size = width * 2
        handle.seek(asset[6] + frame * _FRAME_SIZE)
        raw = _read_exact(handle, _FRAME_SIZE)
        if raw is None:
            return False
        offset = _u32(raw)
        expected_size = _u32(raw, 4)
        if (expected_size != height * (mask_size + row_size) or
                offset < opened[4] or offset + expected_size > opened[5]):
            return False
        handle.seek(offset)
        base_x = x + asset[3] * scale
        base_y = y + asset[4] * scale
        for row in range(height):
            mask = _read_exact(handle, mask_size)
            pixels = _read_exact(handle, row_size)
            if mask is None or pixels is None:
                return False
            column = 0
            while column < width:
                while column < width and not (mask[column // 8] & (0x80 >> (column % 8))):
                    column += 1
                start = column
                while column < width and (mask[column // 8] & (0x80 >> (column % 8))):
                    column += 1
                if start == column:
                    continue
                if scale == 1:
                    draw._bytearray(
                        base_x + start, base_y + row, column - start, 1,
                        memoryview(pixels)[start * 2:column * 2], False,
                    )
                    continue
                run = start
                while run < column:
                    pixel_offset = run * 2
                    color = pixels[pixel_offset] | (pixels[pixel_offset + 1] << 8)
                    end = run + 1
                    while end < column:
                        next_offset = end * 2
                        next_color = pixels[next_offset] | (pixels[next_offset + 1] << 8)
                        if next_color != color:
                            break
                        end += 1
                    draw._fill_rectangle(
                        base_x + run * scale, base_y + row * scale,
                        (end - run) * scale, scale, color,
                    )
                    run = end
        return True
    finally:
        handle.close()
