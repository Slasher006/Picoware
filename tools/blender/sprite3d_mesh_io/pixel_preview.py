# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview the actual packed asset, without touching the user's scene or files."""
import json
import struct
import tempfile
from pathlib import Path
import bpy
from bpy.props import StringProperty
from .pixelart import rgb
from .codec import srgb_to_linear

_state = None


def stop_pixel_preview():
    global _state
    if bpy.app.timers.is_registered(_tick): bpy.app.timers.unregister(_tick)
    state, _state = _state, None
    if state:
        for image in state['images']:
            if image.name in bpy.data.images: bpy.data.images.remove(image)


def _tick():
    state = _state
    if not state: return None
    if state['window'] not in list(bpy.context.window_manager.windows):
        stop_pixel_preview(); return None
    area = next((a for a in state['window'].screen.areas if a.type == 'IMAGE_EDITOR'), None)
    if area is None:
        stop_pixel_preview(); return None
    if state['index'] + 1 >= len(state['images']) and not state['loop']:
        return .25
    state['index'] = (state['index'] + 1) % len(state['images'])
    area.spaces.active.image = state['images'][state['index']]
    area.tag_redraw()
    return 1/state['fps']


def preview_images(path):
    """Create integer-scaled sheets: each cell has native pixels left, 12x right."""
    data = Path(path).read_bytes()
    magic, version, directions, poses, flags, w, h, fps, _, length = struct.unpack('<4sBBBBHHffI',data[:24])
    if magic != b'PWBB' or version != 1 or len(data) != 24+length: raise ValueError('Invalid preview asset')
    columns = min(4,directions); rows = (directions+columns-1)//columns
    cell_w, cell_h = w*13+24, h*12+24
    width,height = columns*cell_w,rows*cell_h
    images=[]; offset=24
    try:
        for pose in range(poses):
            rgba=[]
            for y in range(height):
                for x in range(width):
                    gray = .12 if (x//8+y//8)%2 else .2
                    rgba.extend((gray,gray,gray,1))
            for direction in range(directions):
                pixels=data[offset:offset+w*h];offset+=w*h
                mask=data[offset:offset+(w*h+7)//8];offset+=(w*h+7)//8
                ox=(direction%columns)*cell_w+8
                oy=(rows-1-direction//columns)*cell_h+12
                for scale,dx in ((1,0),(12,w+8)):
                    for y in range(h):
                        for x in range(w):
                            k=y*w+x
                            if not mask[k//8] & (128>>(k%8)): continue
                            color=tuple(srgb_to_linear(c/255) for c in rgb(pixels[k]))+(1,)
                            for sy in range(scale):
                                for sx in range(scale):
                                    i=((oy+(h-1-y)*scale+sy)*width+ox+dx+x*scale+sx)*4
                                    rgba[i:i+4]=color
            image=bpy.data.images.new('Picoware Pixels — pose %d / %d'%(pose+1,poses),width=width,height=height,float_buffer=True)
            images.append(image);image.pixels.foreach_set(rgba);image.update()
        return images,fps,bool(flags&1)
    except Exception:
        for image in images: bpy.data.images.remove(image)
        raise


class SPRITE3D_OT_preview_pixel_art(bpy.types.Operator):
    bl_idname = 'sprite3d.preview_pixel_art'
    bl_label = 'Preview Exported Pixels'
    bl_description = 'Bake current settings and play exact output pixels in a separate window; regenerate after changing settings'
    settings_json: StringProperty(default='{}', options={'HIDDEN'})

    def execute(self,context):
        global _state
        from .billboard import bake, stop_preview
        images=[]
        try:
            settings = json.loads(self.settings_json)
            if 'error' in settings: raise ValueError(settings['error'])
            existing_window = _state['window'] if _state else None
            stop_preview()
            with tempfile.TemporaryDirectory(prefix='picoware-preview-') as folder:
                path,_=bake(context,str(Path(folder)/'preview.bin'),**settings)
                images,fps,loop=preview_images(path)
            if existing_window in list(context.window_manager.windows):
                window=existing_window
            else:
                before=list(context.window_manager.windows)
                bpy.ops.wm.window_new()
                window=next(w for w in context.window_manager.windows if w not in before)
            area=max(window.screen.areas,key=lambda a:a.width*a.height)
            area.type='IMAGE_EDITOR';area.spaces.active.image=images[0]
            with context.temp_override(window=window,area=area,region=next(r for r in area.regions if r.type=='WINDOW')):
                bpy.ops.image.view_zoom_ratio(ratio=1.0)
            _state=dict(images=images,window=window,index=0,fps=fps,loop=loop)
            bpy.app.timers.register(_tick,first_interval=1/fps)
            self.report({'INFO'},'Exact output: native size at left, 12x at right. Views run left to right; close window or Stop Preview to end.')
            return {'FINISHED'}
        except Exception as exc:
            for image in images:
                if image.name in bpy.data.images:bpy.data.images.remove(image)
            self.report({'ERROR'},str(exc));return {'CANCELLED'}
