# SPDX-License-Identifier: GPL-3.0-or-later
"""Three animated billboards, dark ground and orbiting camera. Esc/Back exits."""
import math, gc, time
if 'asset_paths' not in globals():
    from picoware.system.storage import Storage
    storage = Storage()
    assert storage.mount_vfs(), 'Unable to mount SD card'
    asset_paths = {'dragon':'/sd/dragon.bin', 'oak':'/sd/windoak.bin', 'oak1':'/sd/windoak1.bin'}
from picoware.engine.billboard_asset import load_animation,BillboardAnimation
from engine import Game,Level
from picoware.engine.camera import Camera, CAMERA_THIRD_PERSON
from picoware.engine.engine import GameEngine
from picoware.engine.entity import Entity
from picoware.system.vector import Vector
from picoware.gui.draw import Draw
camera=Camera(position=Vector(0,0,0),direction=Vector(1,0,0),height=.6,perspective=CAMERA_THIRD_PERSON)
draw=Draw();game=Game('Wind Oak',Vector(10,10,10),0xffff,0x001f,camera);level=Level('Oak',Vector(10,10,10),game)
entities=[];assets=[];players=[]
for label,side,path in (('Original',-1.35,asset_paths['oak']),('Dragon',0,asset_paths['dragon']),('Your oak',1.35,asset_paths['oak1'])):
 ent=Entity(label,0,Vector(4,side,0),Vector(1,1,1))
 angle=math.atan2(-side,-4)-math.pi/4
 ent.direction=Vector(math.cos(angle),math.sin(angle),0)
 asset,player=load_animation(path,ent,1)
 level.entity_add(ent);entities.append(ent);assets.append(asset);players.append(player)
 print('LOADED',label,asset)
game.set_current_level(level);game.is_active=True
level.set_clear_allowed(False)
runner=GameEngine(game,30,False)

# A horizontal ground plane projects below the horizon with this level camera.
# The world-space grid is projected and clipped before the sprites are drawn.
def ground_line(ax,ay,bx,by,cx,cy,dx,dy):
 def camera_point(x,y):
  wx,wy=x-cx,y-cy
  return -wx*dy+wy*dx,wx*dx+wy*dy
 x1,z1=camera_point(ax,ay);x2,z2=camera_point(bx,by)
 near=.1
 if z1<near and z2<near:return
 if z1<near:
  f=(near-z1)/(z2-z1);x1+=f*(x2-x1);z1=near
 if z2<near:
  f=(near-z2)/(z1-z2);x2+=f*(x1-x2);z2=near
 x1,y1=160+320*x1/z1,160+192/z1
 x2,y2=160+320*x2/z2,160+192/z2
 vx,vy=x2-x1,y2-y1
 lo,hi=0.,1.
 for p,q in ((-vx,x1),(vx,319-x1),(-vy,y1-160),(vy,319-y1)):
  if p==0:
   if q<0:return
  else:
   r=q/p
   if p<0:lo=max(lo,r)
   else:hi=min(hi,r)
   if lo>hi:return
 draw.line_custom(Vector(int(x1+lo*vx),int(y1+lo*vy)),Vector(int(x1+hi*vx),int(y1+hi*vy)),0x2124)

def ground(cx,cy,dx,dy):
 draw.fill_screen(0x212a)
 draw.fill_rectangle(Vector(0,160),Vector(320,160),0x18e3)
 for offset in range(-12,13):
  ground_line(4+offset,-12,4+offset,12,cx,cy,dx,dy)
  ground_line(-8,offset,16,offset,cx,cy,dx,dy)

from picoware.system.input import Input
from picoware.system.buttons import BUTTON_BACK,BUTTON_ESCAPE
inp=Input();last=time.ticks_ms();orbit=0.;frames=0
print('GROVE_DEVICE_RUNNING_ESC_TO_STOP')
try:
 while True:
  if inp.read_non_blocking() in (BUTTON_BACK,BUTTON_ESCAPE):break
  now=time.ticks_ms();elapsed=time.ticks_diff(now,last)/1000;last=now
  orbit=(orbit+elapsed)%20
  for player in players:player.update(elapsed)
  angle=math.pi+orbit*2*math.pi/20
  ca,sa=math.cos(angle),math.sin(angle)
  camera.position=Vector(4+4*ca,4*sa,0)
  camera.direction=Vector(-ca,-sa,0)
  camera.plane=Vector(.66*sa,-.66*ca,0)
  ground(4+4*ca,4*sa,-ca,-sa)
  runner.run_async(False)
  draw.swap()  # clear_allowed=False also disables the engine display swap.
  frames+=1
  if frames in (1,30,120):print('GROVE_RENDERED_FRAMES',frames)
  time.sleep_ms(10)
finally:
 for ent in entities:ent.set_billboard(None);ent.is_player=True
 level.clear();game.set_current_level(None);game.is_active=False
print('GROVE_STOPPED')
