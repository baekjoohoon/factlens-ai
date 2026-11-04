from PIL import Image
from pathlib import Path
S = Path("C:/AI_Agent/FactLens_AI/_shots"); C = Path("C:/AI_Agent/KIC_FactLens/final/_shots")
im = Image.open(S/"dart_viewer.png")
im.crop((505*2, 160*2, 1005*2, 480*2)).save(C/"dart_cover.png")
print(Image.open(C/"dart_cover.png").size)
