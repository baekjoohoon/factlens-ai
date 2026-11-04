from PIL import Image
from pathlib import Path
S = Path("C:/AI_Agent/FactLens_AI/_shots"); C = Path("C:/AI_Agent/KIC_FactLens/final/_shots")
im = Image.open(S/"app_report.png")
im.crop((330*2, 366*2, 1300*2, 898*2)).save(C/"app_result.png")
print(Image.open(C/"app_result.png").size)
