"""xlsx 读写，纯标准库（本机无 openpyxl/pandas）。"""
import zipfile, re, html
from xml.etree import ElementTree as ET

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def _colnum(ref):
    n = 0
    for ch in re.match(r'([A-Z]+)', ref).group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read(path, sheet='xl/worksheets/sheet1.xml'):
    """返回 list[dict[colidx] = value]，第 0 行是表头。"""
    z = zipfile.ZipFile(path)
    sst = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')):
            sst.append(''.join(t.text or '' for t in si.iter(NS + 't')))
    rows = []
    for row in ET.fromstring(z.read(sheet)).iter(NS + 'row'):
        cells = {}
        for c in row.iter(NS + 'c'):
            v = c.find(NS + 'v')
            isel = c.find(NS + 'is')
            if c.get('t') == 's' and v is not None:
                val = sst[int(v.text)]
            elif isel is not None:
                val = ''.join(t.text or '' for t in isel.iter(NS + 't'))
            elif v is not None:
                val = v.text
            else:
                val = ''
            cells[_colnum(c.get('r'))] = val
        rows.append(cells)
    return rows


_XML_BAD = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')


def _esc(s):
    """转义并剥离 XML 1.0 不允许的控制字符（线上日志文本里常见 \x0b 等）。"""
    s = _XML_BAD.sub('', str(s))
    return html.escape(s, quote=False)


def _colname(i):
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write(path, rows):
    """rows: list[list[str]]，第一行表头。全部以 inlineStr 写出，避免 sharedStrings。"""
    sheet = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             '<sheetData>']
    for ri, row in enumerate(rows, start=1):
        sheet.append(f'<row r="{ri}">')
        for ci, val in enumerate(row):
            if val is None or val == '':
                continue
            ref = f'{_colname(ci)}{ri}'
            sheet.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                         f'{_esc(val)}</t></is></c>')
        sheet.append('</row>')
    sheet.append('</sheetData></worksheet>')

    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
          'officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-'
          'officedocument.spreadsheetml.worksheet+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wbrels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
              'relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', ct)
        z.writestr('_rels/.rels', rels)
        z.writestr('xl/workbook.xml', wb)
        z.writestr('xl/_rels/workbook.xml.rels', wbrels)
        z.writestr('xl/worksheets/sheet1.xml', ''.join(sheet))
