from PIL import Image
from pathlib import Path

S = Path("C:/AI_Agent/FactLens_AI/_shots")
C = S / "crops"
C.mkdir(exist_ok=True)

def crop(src, box, out, scale=2):
    im = Image.open(S / src)
    x0, y0, x1, y1 = [v * scale for v in box]
    im.crop((x0, y0, x1, y1)).save(C / out)
    print(out, Image.open(C / out).size)

crop("app_report.png", (340, 170, 1230, 370), "app_head.png")
crop("app_report.png", (340, 388, 1230, 618), "app_claim.png")
crop("app_report.png", (330, 80, 1240, 1000), "app_panel.png")
crop("dart_is.png", (370, 315, 1000, 470), "dart_table.png")
crop("dart_is.png", (380, 148, 1000, 312), "dart_head.png")
