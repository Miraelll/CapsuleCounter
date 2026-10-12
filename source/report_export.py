"""Portable XLSX report writer: embedded images, no Excel installation needed."""
from pathlib import Path
from datetime import datetime
import io,math,re,zipfile,uuid
from xml.sax.saxutils import escape
import numpy as np
import engine

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE='http://schemas.openxmlformats.org/package/2006/relationships'
XML='<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'

def column(n):
    s=''
    while n:n,a=divmod(n-1,26);s=chr(65+a)+s
    return s

def cell(row,col,value,style=0):
    ref=f'{column(col)}{row}'
    if value is None:return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value,(float,int,np.integer,np.floating)) and not isinstance(value,bool):
        if not math.isfinite(float(value)):raise ValueError('Некорректное число в отчёте.')
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',str(value))[:32767]
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'

def sheet(rows,widths,header=1,merges=(),drawing=False):
    count=len(rows)
    if count>1048576:raise ValueError('Слишком много измерений для одного листа Excel.')
    data=[]
    for i,values in enumerate(rows,1):
        height=42 if i==header else 30
        cells=''.join(cell(i,j,v,1 if i==header else 2 if isinstance(v,(float,np.floating)) else 0) for j,v in enumerate(values,1))
        data.append(f'<row r="{i}" ht="{height}" customHeight="1">{cells}</row>')
    cols=''.join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i,width in enumerate(widths,1))
    merge='<mergeCells count="'+str(len(merges))+'">'+''.join(f'<mergeCell ref="{m}"/>' for m in merges)+'</mergeCells>' if merges else ''
    filt=f'<autoFilter ref="A{header}:{column(len(widths))}{count}"/>' if not drawing and count>=header else ''
    return XML+f'<worksheet xmlns="{NS}" xmlns:r="{REL}"><sheetViews><sheetView workbookViewId="0" zoomScale="85"><pane ySplit="{header}" topLeftCell="A{header+1}" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/><cols>{cols}</cols><sheetData>'+''.join(data)+f'</sheetData>{filt}{merge}<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>'+('<drawing r:id="rId1"/>' if drawing else '')+'</worksheet>'

STYLES=XML+f'''<styleSheet xmlns="{NS}">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/><color rgb="FF162B3A"/></font><font><b/><sz val="11"/><name val="Calibri"/><color rgb="FFFFFFFF"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF187B8D"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment vertical="center"/></xf></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''

def write_xlsx(path,records,images):
    summary=[['Результаты измерений капсул'],['Снимок текущей разметки. Автоматические отметки требуют проверки; масштаб каждого фото указан отдельно.'],
        ['Площадь — независимый анализ пикселей. Количество по площади приблизительное; перекрытия учитываются один раз. Пустое значение означает, что измерение не выполнено.'],[],
        ['№ фото','Файл','Состояние','Капсул','Проверено','Средний диаметр (мкм)','Площадь (мкм²)','Покрытие (%)','Количество по площади ≈','Образец (мкм²)','Диаметр поля (мкм)','Калибровка']]
    measurements=[['№ фото']+engine.HEADERS+['Диаметр поля (мкм)','Калибровка']]
    image_rows=[['Отмеченные фотографии'],['Изображения встроены в этот файл. Полный размер сохранён в папке «Картинки».']]
    anchors=[];image_blobs=[]
    for i,(record,im) in enumerate(zip(records,images),1):
        r=record;objects=r['objects'];done=r['analyzed'] or r['dirty'] or bool(objects)
        s=engine.scale(r['field'],r['um']);area=(r.get('coverage') or {}).get('statistics',{})
        summary.append([i,Path(r['path']).name,'Есть измерения' if done else 'Не измерена',len(objects) if done else None,
            sum(o.reviewed for o in objects) if done else None,
            float(np.mean([o.diameter*s for o in objects])) if objects and s else None,
            area.get('area_um2'),area.get('coverage_percent'),area.get('estimated_count'),area.get('reference_area_um2'),r['um'],r['calibration']])
        for values in engine.rows(objects,r['field'],r['um'],Path(r['path']).name):measurements.append([i]+values+[r['um'],r['calibration']])
        title_row=len(image_rows)+1
        image_rows.append([f"Фото {i}: {Path(r['path']).name}"])
        image_rows.append([f"Капсул: {len(objects) if done else 'не измерено'}. Проверено: {sum(o.reviewed for o in objects)}. {r['calibration']}"])
        preview=im.copy();preview.thumbnail((850,650))
        image_row=len(image_rows)
        buffer=io.BytesIO();preview.save(buffer,format='PNG');image_blobs.append(buffer.getvalue())
        anchors.append(f'''<xdr:oneCellAnchor><xdr:from><xdr:col>0</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{image_row}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:ext cx="{preview.width*9525}" cy="{preview.height*9525}"/><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{i}" name="Фото {i}" descr="Отмеченные капсулы"/><xdr:cNvPicPr/></xdr:nvPicPr><xdr:blipFill><a:blip r:embed="rId{i}"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill><xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{preview.width*9525}" cy="{preview.height*9525}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic><xdr:clientData/></xdr:oneCellAnchor>''')
        image_rows.extend([[] for _ in range(math.ceil(preview.height/40)+2)])
    names=['Сводка','Капсулы','Изображения']
    workbook=XML+f'<workbook xmlns="{NS}" xmlns:r="{REL}"><bookViews><workbookView/></bookViews><sheets>'+''.join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i,name in enumerate(names,1))+'</sheets></workbook>'
    relationships=XML+f'<Relationships xmlns="{PACKAGE}">'+''.join(f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,4))+f'<Relationship Id="rId4" Type="{REL}/styles" Target="styles.xml"/></Relationships>'
    content=XML+'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,4))+'<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/></Types>'
    temporary=Path(str(path)+'.tmp')
    try:
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            z.writestr('[Content_Types].xml',content)
            z.writestr('_rels/.rels',XML+f'<Relationships xmlns="{PACKAGE}"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            z.writestr('xl/workbook.xml',workbook);z.writestr('xl/_rels/workbook.xml.rels',relationships);z.writestr('xl/styles.xml',STYLES)
            z.writestr('xl/worksheets/sheet1.xml',sheet(summary,[9,40,20,12,14,20,20,18,22,20,20,42],header=5,merges=['A1:L1','A2:L2','A3:L3']))
            z.writestr('xl/worksheets/sheet2.xml',sheet(measurements,[9,40,9,18,18,23,23,23,23,23,23,20,15,48,22,42]))
            z.writestr('xl/worksheets/sheet3.xml',sheet(image_rows,[125],header=1,drawing=True))
            z.writestr('xl/worksheets/_rels/sheet3.xml.rels',XML+f'<Relationships xmlns="{PACKAGE}"><Relationship Id="rId1" Type="{REL}/drawing" Target="../drawings/drawing1.xml"/></Relationships>')
            z.writestr('xl/drawings/drawing1.xml',XML+f'<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="{REL}">'+''.join(anchors)+'</xdr:wsDr>')
            z.writestr('xl/drawings/_rels/drawing1.xml.rels',XML+f'<Relationships xmlns="{PACKAGE}">'+''.join(f'<Relationship Id="rId{i}" Type="{REL}/image" Target="../media/image{i}.png"/>' for i in range(1,len(images)+1))+'</Relationships>')
            for i,data in enumerate(image_blobs,1):z.writestr(f'xl/media/image{i}.png',data)
        temporary.replace(path)
    finally:
        if temporary.exists():temporary.unlink()

def export_session(records,folder):
    destination=Path(folder)/('Результаты-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    pictures=destination/'Картинки';projects=destination/'Проекты'
    pictures.mkdir(parents=True);projects.mkdir()
    images=[]
    for i,r in enumerate(records,1):
        if r['rgb'] is None:r['rgb']=engine.read_image(r['path']);r['field']=engine.optical_field(r['rgb'])
        im=engine.annotated(r['rgb'],r['objects'],labels=True);images.append(im)
        name=f'{i:03d}-'+re.sub(r'[<>:"/\\|?*]','_',Path(r['path']).stem)
        im.save(pictures/(name+'-marked.png'))
        settings=dict(r['settings']);settings['measurement_done']=bool(r['analyzed'] or r['dirty'] or r['objects'])
        engine.save_project(projects/(name+'.capsules'),r['rgb'],r['objects'],r['field'],r['um'],r['calibration'],settings,r['path'])
    write_xlsx(destination/'Результаты.xlsx',records,images)
    (destination/'Прочитайте.txt').write_text('Результаты.xlsx содержит сводку, все измерения и встроенные отмеченные фотографии. Картинки/ — PNG в исходном разрешении. Проекты/ — для продолжения работы. Пустое количество означает «не измерено».\n',encoding='utf-8')
    return destination
