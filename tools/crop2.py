from PIL import Image
from pathlib import Path
S = Path("C:/AI_Agent/FactLens_AI/_shots"); C = S/"crops"
im = Image.open(S/"app_report.png")
im.crop((335*2, 358*2, 1246*2, 892*2)).save(C/"app_result.png")
print("app_result", Image.open(C/"app_result.png").size)
im2 = Image.open(S/"dart_is.png")
im2.crop((370*2, 300*2, 1010*2, 480*2)).save(C/"dart_table.png")
print("dart_table", Image.open(C/"dart_table.png").size)
