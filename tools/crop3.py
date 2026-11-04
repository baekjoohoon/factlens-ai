from PIL import Image
from pathlib import Path
S = Path("C:/AI_Agent/FactLens_AI/_shots"); C = Path("C:/AI_Agent/KIC_FactLens/final/_shots")
im = Image.open(S/"dart_viewer.png")
im.crop((500*2, 170*2, 1010*2, 640*2)).save(C/"dart_cover.png")
print("dart_cover", Image.open(C/"dart_cover.png").size)
im2 = Image.open(S/"dart_is.png")
im2.crop((380*2, 148*2, 1010*2, 310*2)).save(C/"dart_head.png")
print("dart_head", Image.open(C/"dart_head.png").size)
