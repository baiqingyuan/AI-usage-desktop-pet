"""Optional build tool: draw previews from the app's widget/vector layout, not screen capture.

Requires Pillow only for this tool; the desktop app uses the standard library.
"""
import math
from pathlib import Path
import sys
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
import core

SCALE = 2


def font_info(widget, specification):
    font = tkfont.Font(root=widget, font=specification)
    actual = font.actual()
    size = actual['size']
    pixels = size*float(widget.tk.call('tk', 'scaling')) if size > 0 else -size
    path = Path(r'C:\Windows\Fonts')/('msyhbd.ttc' if actual['weight'] == 'bold' else 'msyh.ttc')
    return ImageFont.truetype(str(path), max(1,round(pixels*SCALE))), font.metrics('linespace')*SCALE


def write_text(draw, xy, text, font, color, anchor='nw', width=None, line_space=None):
    lines = []
    for part in str(text).split('\n'):
        line = ''
        for character in part:
            if width and line and draw.textlength(line+character, font=font) > width:
                lines.append(line)
                line = ''
            line += character
        lines.append(line)
    line_space = line_space or font.size*1.4
    x,y = xy
    if anchor in ('w','center','e'):
        y -= len(lines)*line_space/2
    elif anchor in ('sw','s','se'):
        y -= len(lines)*line_space
    for line in lines:
        xx = x
        length = draw.textlength(line, font=font)
        if anchor in ('center','n','s'):
            xx -= length/2
        elif anchor in ('e','ne','se'):
            xx -= length
        ink = draw.textbbox((0,0), line, font=font, anchor='lt')
        draw.text((xx,y+(line_space-ink[3])/2), line, font=font, fill=color, anchor='lt')
        y += line_space


def canvas_items(widget, draw, x, y):
    for item in widget.find_all():
        kind = widget.type(item)
        coords = widget.coords(item)
        fill = widget.itemcget(item, 'fill') if kind != 'window' else ''
        def points(values):
            return [((x+xx)*SCALE,(y+yy)*SCALE) for xx,yy in zip(values[::2],values[1::2])]
        if kind == 'polygon':
            vertices = list(zip(coords[::2],coords[1::2]))
            if widget.itemcget(item, 'smooth') not in ('0','false',''):
                smooth = []
                for i,p in enumerate(vertices):
                    before, after = vertices[i-1], vertices[(i+1)%len(vertices)]
                    a = ((before[0]+p[0])/2,(before[1]+p[1])/2)
                    b = ((after[0]+p[0])/2,(after[1]+p[1])/2)
                    for step in range(17):
                        t = step/16
                        smooth.append(((1-t)**2*a[0]+2*t*(1-t)*p[0]+t*t*b[0],
                                       (1-t)**2*a[1]+2*t*(1-t)*p[1]+t*t*b[1]))
                vertices = smooth
            draw.polygon([((x+xx)*SCALE,(y+yy)*SCALE) for xx,yy in vertices], fill=fill or None)
        elif kind in ('oval','rectangle'):
            box = [(x+coords[0])*SCALE,(y+coords[1])*SCALE,
                   (x+coords[2])*SCALE,(y+coords[3])*SCALE]
            (draw.ellipse if kind == 'oval' else draw.rectangle)(box, fill=fill or None)
        elif kind == 'line':
            draw.line(points(coords), fill=fill, width=max(1,round(float(widget.itemcget(item,'width'))*SCALE)))
        elif kind == 'arc':
            x1,y1,x2,y2 = coords
            start, extent = float(widget.itemcget(item,'start')), float(widget.itemcget(item,'extent'))
            vertices = []
            for step in range(41):
                angle = math.radians(start+extent*step/40)
                vertices.extend([(x1+x2)/2+(x2-x1)/2*math.cos(angle),
                                 (y1+y2)/2-(y2-y1)/2*math.sin(angle)])
            draw.line(points(vertices), fill=widget.itemcget(item,'outline'),
                      width=max(1,round(float(widget.itemcget(item,'width'))*SCALE)))
        elif kind == 'text':
            font, lines = font_info(widget, widget.itemcget(item,'font'))
            write_text(draw, ((x+coords[0])*SCALE,(y+coords[1])*SCALE),
                       widget.itemcget(item,'text'), font, fill, widget.itemcget(item,'anchor'),
                       line_space=lines)


def render(window, path, transparent=False):
    window.update_idletasks()
    width, height = window.winfo_width(), window.winfo_height()
    image = Image.new('RGBA', (width*SCALE,height*SCALE), (0,0,0,0) if transparent else app.BG)
    origin_x,origin_y = window.winfo_rootx(),window.winfo_rooty()
    def visit(widget, clip):
        if not widget.winfo_ismapped():
            return
        x,y = widget.winfo_rootx()-origin_x,widget.winfo_rooty()-origin_y
        w,h = widget.winfo_width(),widget.winfo_height()
        bounds = (max(clip[0],x), max(clip[1],y), min(clip[2],x+w), min(clip[3],y+h))
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            return
        layer = Image.new('RGBA', image.size, (0,0,0,0))
        draw = ImageDraw.Draw(layer)
        keys = widget.keys()
        if 'background' in keys:
            bg = str(widget.cget('background'))
            if bg and bg != '#ff00ff':
                draw.rectangle((x*SCALE,y*SCALE,(x+w)*SCALE,(y+h)*SCALE),fill=bg)
        if isinstance(widget, tk.Canvas):
            canvas_items(widget, draw, x, y)
        elif isinstance(widget, (tk.Label,tk.Button,tk.Checkbutton)):
            font,lines = font_info(widget, widget.cget('font'))
            anchor = str(widget.cget('anchor'))
            xx = x+5 if anchor in ('w','nw','sw') else x+w-5 if anchor in ('e','ne','se') else x+w/2
            wrap = int(float(str(widget.cget('wraplength')))) if 'wraplength' in keys else 0
            write_text(draw, (xx*SCALE,(y+h/2)*SCALE), widget.cget('text'), font,
                       str(widget.cget('foreground')), anchor if anchor in ('w','e') else 'center',
                       width=wrap*SCALE if wrap else None,line_space=lines)
        elif isinstance(widget, tk.Text):
            font, lines = font_info(widget, widget.cget('font'))
            padx, pady = int(widget.cget('padx')), int(widget.cget('pady'))
            write_text(draw, ((x+padx)*SCALE, (y+pady)*SCALE),
                       widget.get('1.0', 'end-1c'), font, str(widget.cget('foreground')),
                       width=(w-2*padx-10)*SCALE,
                       line_space=lines+(int(widget.cget('spacing1'))+int(widget.cget('spacing3')))*SCALE)
        elif widget.winfo_class() == 'TProgressbar':
            draw.rounded_rectangle((x*SCALE,y*SCALE,(x+w)*SCALE,(y+h)*SCALE),radius=4*SCALE,fill=app.PANEL)
            fraction = min(1, max(0, float(widget.cget('value'))/float(widget.cget('maximum'))))
            if fraction:
                draw.rounded_rectangle((x*SCALE,y*SCALE,(x+w*fraction)*SCALE,(y+h)*SCALE),radius=4*SCALE,fill=app.GREEN)
        elif widget.winfo_class() in ('TEntry','TCombobox'):
            draw.rounded_rectangle((x*SCALE,y*SCALE,(x+w)*SCALE,(y+h)*SCALE),radius=7*SCALE,
                                   fill=app.CARD,outline='#c7ddcd',width=SCALE)
            font,lines = font_info(widget, (app.FONT,12))
            value = widget.get()
            if widget.winfo_class() == 'TEntry' and str(widget.cget('show')):
                value = '•'*len(value)
            write_text(draw, ((x+9)*SCALE,(y+h/2)*SCALE), value, font, app.TEXT, 'w',line_space=lines)
            if widget.winfo_class() == 'TCombobox':
                draw.line([((x+w-20)*SCALE,(y+h/2-2)*SCALE),((x+w-16)*SCALE,(y+h/2+2)*SCALE),
                           ((x+w-12)*SCALE,(y+h/2-2)*SCALE)],fill=app.MUTED,width=2*SCALE)
        elif widget.winfo_class() == 'TScrollbar':
            draw.rounded_rectangle((x*SCALE,y*SCALE,(x+w)*SCALE,(y+h)*SCALE),radius=6*SCALE,fill=app.PANEL)
            first,last = widget.get()
            draw.rounded_rectangle((x*SCALE,(y+h*first)*SCALE,(x+w)*SCALE,(y+h*last)*SCALE),
                                   radius=5*SCALE,fill='#adc8b5')
        elif widget.winfo_class() == 'TNotebook':
            font,lines = font_info(widget,(app.FONT,11))
            selected = widget.select()
            for i,tab in enumerate(widget.tabs()):
                xx = x+i*(w/3)
                draw.rounded_rectangle((xx*SCALE,y*SCALE,(xx+w/3-4)*SCALE,(y+36)*SCALE),
                                       radius=8*SCALE,fill=app.CARD if tab == selected else app.PANEL)
                write_text(draw,((xx+w/6)*SCALE,(y+18)*SCALE),widget.tab(tab,'text'),font,app.TEXT,
                           'center',line_space=lines)
        region = tuple(round(value*SCALE) for value in bounds)
        image.alpha_composite(layer.crop(region),(region[0],region[1]))
        for child in widget.winfo_children():
            visit(child,bounds)
    visit(window,(0,0,width,height))
    image.save(path)


if __name__ == '__main__':
    if len(sys.argv) > 2:
        core.SETTINGS = Path(sys.argv[2])
    root = tk.Tk()
    dashboard = app.Dashboard(root, offline=True)
    for timer in root.tk.call('after','info'):
        root.after_cancel(timer)
    saved = core.SNAPSHOT
    snapshot = Path(sys.argv[1]) if len(sys.argv)>1 else saved
    if snapshot.exists():
        import json
        dashboard.results = {r['id']:r for r in json.loads(snapshot.read_text(encoding='utf-8'))}
    dashboard.surface.visible_bubble = True
    dashboard.render()
    dashboard.surface.animate()
    root.update()
    render(root,ROOT/'soft-preview.png',True)
    popup = dashboard.surface.provider_menu()
    root.update()
    render(popup,ROOT/'provider-menu-preview.png',True)
    popup.escape()
    popup = dashboard.surface.menu()
    root.update()
    render(popup,ROOT/'context-menu-preview.png',True)
    popup.escape()
    dashboard.surface.visible_bubble = False
    dashboard.render()
    dashboard.surface.animate()
    root.update()
    render(root,ROOT/'compact-preview.png',True)
    dashboard.open_details('codex')
    root.update()
    render(dashboard.details,ROOT/'preview.png')
    dashboard.details.selector.open_menu()
    root.update()
    render(dashboard.details,ROOT/'main-menu-preview.png')
    dashboard.details.selector.close_menu()
    dashboard.open_settings()
    root.update()
    render(dashboard.settings,ROOT/'settings-preview.png')
    dashboard.settings.destroy()
    dashboard.details.close()
    for timer in root.tk.call('after','info'):
        root.after_cancel(timer)
    root.destroy()
    print('Layout previews generated from the widget tree and vector source')
