#!/usr/bin/env python3
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

W,H=1200,627
BG=(9,24,39); NAVY2=(14,39,57); WHITE=(246,243,234)
TEAL=(105,195,187); GOLD=(201,152,53); MUTED=(160,181,196)

im=Image.new("RGB",(W,H),BG)
d=ImageDraw.Draw(im)

for y in range(H):
    t=y/(H-1)
    c=tuple(int(BG[i]*(1-t)+NAVY2[i]*t) for i in range(3))
    d.line([(0,y),(W,y)],fill=c)

bars=[(760,320,28,180),(806,255,28,245),(852,350,28,150),(898,210,28,290),
      (944,280,28,220),(990,145,28,355),(1036,235,28,265),(1082,92,28,408)]
for x,y,w,h in bars:
    d.rounded_rectangle([x,y,x+w,y+h],radius=5,fill=(20,78,91))

cx,cy,r=1010,170,140
d.ellipse([cx-r,cy-r,cx+r,cy+r],outline=(40,126,143),width=2)
for scale in [0.45,0.72]:
    d.ellipse([cx-r,cy-int(r*scale),cx+r,cy+int(r*scale)],outline=(36,112,128),width=1)
for xoff in [-65,65]:
    d.ellipse([cx-r+xoff,cy-r,cx+r+xoff,cy+r],outline=(29,91,107),width=1)

nodes=[(900,125,GOLD),(958,82,GOLD),(1095,140,GOLD),(875,210,TEAL),(1048,198,TEAL)]
for x,y,c in nodes:
    d.line([(815,270),(x,y)],fill=(32,106,121),width=1)
    d.ellipse([x-5,y-5,x+5,y+5],fill=c)

for ox,oy,angle in [(920,360,-8),(1040,350,6)]:
    paper=Image.new("RGBA",(230,240),(0,0,0,0))
    pd=ImageDraw.Draw(paper)
    pd.rounded_rectangle([0,0,218,228],radius=6,fill=(171,190,203,150))
    for yy,ln in [(38,165),(58,145),(78,176),(98,130)]:
        pd.line([(24,yy),(24+ln,yy)],fill=(48,76,96,130),width=4)
    for i,h in enumerate([40,60,78,98]):
        x=35+i*32
        pd.rectangle([x,185-h,x+18,185],fill=(38,70,88,165))
    paper=paper.rotate(angle,expand=True,resample=Image.Resampling.BICUBIC)
    im.paste(paper,(ox,oy),paper)

def getfont(path,size):
    try:
        return ImageFont.truetype(path,size=size)
    except Exception:
        return ImageFont.load_default()

serif_b=getfont("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",72)
serif=getfont("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",38)
serif_s=getfont("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",25)
sans=getfont("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",20)
sans_b=getfont("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",20)

x=62
d.text((x,65),"Corporate",font=serif_b,fill=WHITE)
d.text((x,145),"Finance Updates",font=serif_b,fill=WHITE)
d.rounded_rectangle([66,272,136,277],radius=2,fill=GOLD)
d.text((x,305),"Bi-weekly research digest",font=serif,fill=TEAL)
d.text((x,365),"New papers, working papers, conferences, and jobs",font=serif_s,fill=WHITE)
d.text((x,400),"in corporate finance",font=serif_s,fill=WHITE)

labels=["Climate","Cybersecurity","AI","ESG","Political Geography"]
positions=[62,178,340,402,478]
bullets=[157,320,382,456]
for i,(lab,xx) in enumerate(zip(labels,positions)):
    d.text((xx,480),lab,font=sans,fill=WHITE)
    if i<len(bullets):
        bx=bullets[i]
        d.ellipse([bx-4,484,bx+4,492],fill=GOLD)

d.line([(62,542),(770,542)],fill=(82,105,121),width=1)
d.text((62,565),"Curated by",font=sans,fill=MUTED)
d.text((176,565),"Efstathios Magerakis",font=sans_b,fill=GOLD)

out=Path(__file__).resolve().parents[1]/"linkedin-preview.jpg"
im.save(out,"JPEG",quality=90,optimize=True,progressive=True)
print(f"Wrote {out} ({W}x{H})")
