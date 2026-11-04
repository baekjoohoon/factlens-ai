from PIL import Image
from pathlib import Path
S = Path("C:/AI_Agent/FactLens_AI/_shots"); C = Path("C:/AI_Agent/KIC_FactLens/final2/_shots")
C.mkdir(parents=True, exist_ok=True)
im = Image.open(S/"app_report_llm.png")
im.crop((330*2, 366*2, 1300*2, 898*2)).save(C/"app_result.png")
print("app_result", Image.open(C/"app_result.png").size)
im.crop((330*2, 150*2, 1300*2, 480*2)).save(C/"app_head.png")
print("app_head", Image.open(C/"app_head.png").size)
