"""Экспорт и импорт показаний в Excel (.xlsx). Нужна библиотека openpyxl."""
from datetime import datetime

HEADERS = ["Дата и время", "Датчик", "Температура, °C", "Влажность воздуха, %", "Датчик воды (0–1000)",
           "Влажность почвы, %", "Засолённость, dS/m", "pH", "Азот N, mg/kg", "Фосфор P, mg/kg", "Калий K, mg/kg"]
TIME_FORMATS = ["%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"]


def export_points(path, points, sensor_name):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Показания"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4F8FF7")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for p in points:
        water = p.get("water")
        ws.append([
            datetime.fromtimestamp(p["ts"]),
            sensor_name,
            _round(p.get("temperature"), 2),
            _round(p.get("humidity"), 2),
            _round(water, 0),
            _round(water / 10 if water is not None else None, 1),
            _round(p.get("salinity"), 2),
            _round(p.get("ph"), 2),
            _round(p.get("n"), 0),
            _round(p.get("p"), 0),
            _round(p.get("k"), 0),
        ])
    for row in ws.iter_rows(min_row=2):
        row[0].number_format = "DD.MM.YYYY HH:MM:SS"
    widths = [22, 30, 16, 20, 20, 18, 18, 10, 16, 16, 16]
    for i, width in enumerate(widths):
        ws.column_dimensions[chr(ord("A") + i)].width = width
    ws.freeze_panes = "A2"
    wb.save(path)


def _round(value, digits):
    return None if value is None else round(float(value), digits)


def _parse_time(value):
    if isinstance(value, datetime):
        return value.timestamp()
    text = str(value).strip() if value is not None else ""
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    raise ValueError("дата не распознана")


def read_import(path):
    """Читает файл, сам находит нужные столбцы по названиям.
    Возвращает (список (время, значения), число пропущенных строк)."""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    if not header:
        raise ValueError("Файл пустой")
    names = [str(h).strip().lower() if h is not None else "" for h in header]

    def find(*keys):
        for i, name in enumerate(names):
            if any(k in name for k in keys):
                return i
        return None

    cols = {
        "time": find("дата", "время", "date", "time"),
        "temp": find("температура", "temperature"),
        "hum": find("влажность воздуха", "humidity"),
        "water": find("датчик воды", "water"),
        "water_pct": find("влажность почвы"),
        "salinity": find("засолен", "salin"),
        "ph": find("ph"),
        "n": find("азот"),
        "p": find("фосфор"),
        "k": find("калий"),
    }
    if cols["time"] is None:
        raise ValueError("В файле не найден столбец «Дата и время»")
    if cols["temp"] is None and cols["hum"] is None and cols["water"] is None and cols["water_pct"] is None:
        raise ValueError("В файле нет ни одного показателя (температура, влажность, датчик воды)")

    def num(row, key):
        idx = cols[key]
        if idx is None or idx >= len(row) or row[idx] in (None, ""):
            return None
        return float(str(row[idx]).replace(",", "."))

    out, skipped = [], 0
    for row in rows:
        if not row or all(v is None for v in row):
            continue
        try:
            ts = _parse_time(row[cols["time"]])
            temp, hum = num(row, "temp"), num(row, "hum")
            water = num(row, "water")
            if water is None and num(row, "water_pct") is not None:
                water = num(row, "water_pct") * 10
            if temp is None or hum is None or water is None:
                raise ValueError("нет значения")
            if not (-50 <= temp <= 80 and 0 <= hum <= 100 and 0 <= water <= 1000):
                raise ValueError("значение вне диапазона")
            values = {
                "temperature": round(temp, 2), "humidity": round(hum, 2), "water": int(round(water)),
                "salinity": num(row, "salinity"), "ph": num(row, "ph"), "n": num(row, "n"),
                "p": num(row, "p"), "k": num(row, "k"),
            }
            out.append((ts, values))
        except (ValueError, TypeError, IndexError):
            skipped += 1
    wb.close()
    return out, skipped
