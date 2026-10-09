"""Generate the companion's application icon from its own vector shapes."""
from pathlib import Path
from PIL import Image, ImageDraw

scale = 4
image = Image.new('RGBA', (256*scale, 256*scale), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
def box(values):
    return tuple(int(v*scale) for v in values)
def roundrect(values, radius, color):
    draw.rounded_rectangle(box(values), radius=radius*scale, fill=color)
draw.ellipse(box((3, 3, 253, 253)), fill='#153c37')
draw.ellipse(box((18, 18, 238, 238)), fill='#24574d')
draw.ellipse(box((64, 27, 91, 77)), fill='#b6f3db')
draw.ellipse(box((165, 27, 192, 77)), fill='#b6f3db')
roundrect((45, 57, 211, 215), 53, '#b5efdc')
roundrect((55, 72, 201, 169), 37, '#112f31')
roundrect((87, 101, 104, 133), 8, '#caffec')
roundrect((152, 101, 169, 133), 8, '#caffec')
draw.arc(box((112, 129, 144, 150)), 10, 168, fill='#78d8bb', width=4*scale)
draw.ellipse(box((116, 184, 140, 200)), fill='#e9fff4')
image = image.resize((256, 256), Image.Resampling.LANCZOS)
root = Path(__file__).resolve().parent
image.save(root/'app.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
image.save(root/'app-icon.png')
