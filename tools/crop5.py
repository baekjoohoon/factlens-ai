from PIL import Image
from pathlib import Path
S = Path("C:/AI_Agent/FactLens_AI/_shots"); C = Path("C:/AI_Agent/KIC_FactLens/final/_shots")
im = Image.open(S/"dart_is.png")
im.crop((370*2, 300*2, 1010*2, 462*2)).save(C/"dart_table.png")
print(Image.open(C/"dart_table.png").size)
