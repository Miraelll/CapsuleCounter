"""Image analysis and portable project format for CapsuleCounter."""
from dataclasses import dataclass, asdict
from pathlib import Path
import csv
import json
import math
import hashlib
import cv2
import numpy as np
from PIL import Image, ImageOps, ImageDraw, ImageEnhance

VERSION = '1.3.0'
# Provisional: a 50 um small grid interval occupies approximately 130 px
# in a field approximately 715 px wide in the supplied unmarked photograph.
# The UI exposes and labels this assumption; explicit calibration replaces it.
DEFAULT_FIELD_UM = 275.0

@dataclass
class Capsule:
    x: float
    y: float
    major: float
    minor: float
    angle: float = 0.0
    diameter: float = 0.0
    origin: str = 'automatic'
    reviewed: bool = False
    uncertain: bool = True
    note: str = ''

    def __post_init__(self):
        if self.major < self.minor:
            self.major, self.minor = self.minor, self.major
            self.angle += 90
        self.angle %= 180
        if not self.diameter:
            self.diameter = self.major

    def contains(self, x, y, tolerance=0):
        theta = math.radians(self.angle)
        dx, dy = x-self.x, y-self.y
        u, v = dx*math.cos(theta)+dy*math.sin(theta), -dx*math.sin(theta)+dy*math.cos(theta)
        return (u/(self.major/2+tolerance))**2+(v/(self.minor/2+tolerance))**2 <= 1

    def points(self, n=72):
        theta = math.radians(self.angle)
        t = np.linspace(0, 2*math.pi, n, endpoint=False)
        u, v = self.major/2*np.cos(t), self.minor/2*np.sin(t)
        return np.column_stack([self.x+u*np.cos(theta)-v*np.sin(theta),
                                self.y+u*np.sin(theta)+v*np.cos(theta)])

def read_image(path):
    with Image.open(path) as im:
        if getattr(im, 'n_frames', 1)>1:
            raise ValueError('Многостраничный файл: сохраните нужный кадр отдельно.')
        return np.array(ImageOps.exif_transpose(im).convert('RGB'))

def has_count_marks(rgb):
    yellow=(rgb[:,:,0]>220)&(rgb[:,:,1]>205)&(rgb[:,:,2]<100)
    return int(yellow.sum())>80

def optical_field(rgb):
    gray=cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    blue=((rgb[:,:,2].astype(float)-rgb[:,:,0])>35)&(rgb[:,:,2]>95)
    yy,xx=np.where(blue)
    if len(xx)>200:
        pts=np.column_stack([xx,yy]).astype(float)
        for _ in range(5):
            mat=np.column_stack([2*pts[:,0], 2*pts[:,1], np.ones(len(pts))])
            cx,cy,k=np.linalg.lstsq(mat, (pts**2).sum(1), rcond=None)[0]
            radius=math.sqrt(max(1,k+cx*cx+cy*cy))
            err=np.abs(np.hypot(pts[:,0]-cx,pts[:,1]-cy)-radius)
            pts=pts[err<max(3,np.quantile(err,.7))]
        if radius>min(gray.shape)*.25 and radius<max(gray.shape)*3:
            return float(cx),float(cy),float(radius)
    contours,_=cv2.findContours((gray>55).astype('uint8'),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError('Не удалось найти поле зрения. Снимок должен содержать видимое поле микроскопа.')
    c=max(contours,key=cv2.contourArea)
    if len(c)<5:
        raise ValueError('Не удалось определить круг поля зрения.')
    (cx,cy),(w,h),angle=cv2.fitEllipse(c)
    if min(w,h)<min(gray.shape)*.3 or max(w,h)/min(w,h)>1.4:
        raise ValueError('Поле зрения не найдено. Используйте исходную фотографию с кругом поля зрения.')
    return float(cx),float(cy),float((w+h)/4)

def _fit_boundary(gray,x,y,r):
    """Fit an ellipse to radial edge samples. Flag incomplete/unstable fits."""
    t=np.linspace(0,2*np.pi,72,endpoint=False)
    radii=np.linspace(max(1,r*.65),r*1.4,24)
    xx=(x+np.cos(t)[:,None]*radii[None,:]).astype('float32')
    yy=(y+np.sin(t)[:,None]*radii[None,:]).astype('float32')
    samples=cv2.remap(gray,xx,yy,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REFLECT)
    # An isolated edge (grid or optical border) must not become an entire capsule.
    radial_contrast=np.ptp(samples,axis=1)
    if np.count_nonzero(radial_contrast>7)<28:
        return None
    edge=np.abs(np.gradient(samples.astype(float),axis=1))
    best=edge.argmax(1)
    strength=edge[np.arange(len(t)),best]
    selected=radii[best]
    valid=(strength>max(1.2, float(np.median(strength))*.35))&(radial_contrast>5)
    pts=np.column_stack([x+np.cos(t)*selected,y+np.sin(t)*selected]).astype('float32')
    if valid.sum()<35:
        return Capsule(x,y,2*r,2*r,note='Контур не восстановлен; круговая оценка')
    try:
        (cx,cy),(a,b),ang=cv2.fitEllipse(pts[valid])
        major,minor=max(a,b),min(a,b)
        angle=ang if a>=b else ang+90
        if minor<r*.8 or major>r*3.1 or major/minor>2.2 or math.hypot(cx-x,cy-y)>r*.45:
            return Capsule(x,y,2*r,2*r,note='Неустойчивый контур; круговая оценка')
        # Feret diameter of the recovered outer edge (not the aggregate envelope).
        q=pts[valid]
        feret=float(np.sqrt(((q[:,None,:]-q[None,:,:])**2).sum(2)).max())
        incomplete=bool(valid.sum()<58 or np.std(selected)/max(1,np.mean(selected))>.25)
        return Capsule(float(cx),float(cy),float(major),float(minor),float(angle),feret,
                       uncertain=incomplete,
                       note='Часть контура плохо видна' if incomplete else '')
    except cv2.error:
        return Capsule(x,y,2*r,2*r,note='Контур не восстановлен; круговая оценка')

def detect(rgb, field=None, sensitivity=50, min_size=2., max_size=10., field_um=None):
    if has_count_marks(rgb):
        raise ValueError('На снимке найдены яркие жёлтые отметки подсчёта. Для автоматического анализа откройте соответствующий исходник без отметок.')
    if not 0 <= sensitivity <= 100 or not 0 < min_size < max_size:
        raise ValueError('Проверьте чувствительность и диапазон размеров.')
    if field_um is not None and (not math.isfinite(field_um) or field_um<=0):
        raise ValueError('Диаметр поля должен быть положительным числом.')
    cx,cy,field_r=field or optical_field(rgb)
    fac=378./field_r
    target=(max(1,round(rgb.shape[1]*fac)),max(1,round(rgb.shape[0]*fac)))
    if target[0]*target[1]>12_000_000:
        raise ValueError('Поле зрения слишком мало относительно изображения.')
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    norm=cv2.resize(gray,target,interpolation=cv2.INTER_AREA if fac<1 else cv2.INTER_LINEAR)
    smooth=cv2.GaussianBlur(norm,(3,3),.7)
    if field_um:
        normalized_um=field_um/756.
        lo=max(2,int(min_size/(2*normalized_um)))
        hi=max(lo+1,round(max_size/(2*normalized_um)))
    else:
        lo,hi=2,12
    hi=min(hi,150)
    votes=12.-sensitivity*.06
    yy,xx=np.indices(smooth.shape)
    field_mask=(xx-cx*fac)**2+(yy-cy*fac)**2<(378.-12)**2
    gx=cv2.Sobel(smooth,cv2.CV_32F,1,0);gy=cv2.Sobel(smooth,cv2.CV_32F,0,1)
    magnitude=cv2.magnitude(gx,gy)
    contrast=float(np.percentile(magnitude[field_mask],99)) if field_mask.any() else 90.
    if fac>=.7:
        canny=max(60.,min(85.,contrast*.65))*(1.3-sensitivity*.006)
    else:
        canny=max(25.,min(60.,contrast*.45))*(1.3-sensitivity*.006)
    small_hi=min(hi,max(lo+1,10))
    circles=cv2.HoughCircles(smooth,cv2.HOUGH_GRADIENT,1,max(4,lo*1.4),
                              param1=canny,param2=votes,minRadius=lo,maxRadius=small_hi)
    if hi>small_hi:
        large=cv2.HoughCircles(smooth,cv2.HOUGH_GRADIENT,1,max(6,small_hi),
                              param1=canny,param2=votes*1.3,minRadius=small_hi+1,maxRadius=hi)
        if large is not None:circles=large if circles is None else np.concatenate([circles,large],axis=1)
    result=[]
    edgegray=cv2.GaussianBlur(gray.astype('float32'),(3,3),.65)
    if circles is not None:
        for x,y,r in circles[0]:
            x,y,r=float(x/fac),float(y/fac),float(r/fac)
            if math.hypot(x-cx,y-cy)>field_r-max(3,2/fac): continue
            o=_fit_boundary(edgegray,x,y,r)
            if o is None:continue
            if math.hypot(x-cx,y-cy)>field_r-r:
                o.uncertain=True;o.note='У края поля зрения'
            result.append(o)
    # Closed, isolated dark rings supply a more stable outer contour than Hough.
    bg=cv2.GaussianBlur(norm.astype('float32'),(0,0),9)
    dark=(bg-norm.astype('float32'))>10
    dark[~field_mask]=False
    contours,_=cv2.findContours(dark.astype('uint8'),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)
    isolated=[]
    for c in contours:
        if len(c)<8:continue
        area=cv2.contourArea(c);perimeter=cv2.arcLength(c,True)
        if area<math.pi*lo**2*.5 or area>math.pi*hi**2 or perimeter==0:continue
        hull=cv2.convexHull(c);hull_area=cv2.contourArea(hull)
        if not hull_area or area/hull_area<.94 or 4*math.pi*area/perimeter**2<.66:continue
        (x,y),(a,b),angle=cv2.fitEllipse(c)
        if min(a,b)<=0 or max(a,b)/min(a,b)>2.6:continue
        theta_rad=math.radians(angle)
        centered=c.reshape(-1,2).astype(float)-np.array([x,y])
        u=centered[:,0]*math.cos(theta_rad)+centered[:,1]*math.sin(theta_rad)
        v=-centered[:,0]*math.sin(theta_rad)+centered[:,1]*math.cos(theta_rad)
        radial_error=np.abs(np.sqrt((u/(a/2))**2+(v/(b/2))**2)-1)
        if float(np.mean(radial_error))>.12:continue
        theta=angle if a>=b else angle+90
        p=c.reshape(-1,2).astype(float)/fac
        feret=float(np.sqrt(((p[:,None,:]-p[None,:,:])**2).sum(2)).max())
        isolated.append(Capsule(x/fac,y/fac,max(a,b)/fac,min(a,b)/fac,theta,feret,
                                uncertain=False,note='Замкнутый наружный контур'))
    result=[o for o in result if not any(q.contains(o.x,o.y,tolerance=.25*q.minor) for q in isolated)]+isolated
    # Fit refinement can converge two nearby detections to the same object.
    kept=[]
    for o in sorted(result,key=lambda o:o.minor):
        if any(math.hypot(o.x-q.x,o.y-q.y)<.35*max(o.minor,q.minor) for q in kept):continue
        kept.append(o)
    for i,o in enumerate(kept):
        if any(i!=j and math.hypot(o.x-q.x,o.y-q.y)<.52*(o.major+q.major) for j,q in enumerate(kept)):
            o.uncertain=True
            if not o.note:o.note='Скопление: проверить границы'
    return sorted(kept,key=lambda o:(round(o.y/15),o.x))

def scale(field,field_um):
    return field_um/(2*field[2]) if field_um else None

def region_indices(capsules,kind,start,end):
    """Select annotations by their centers, including the region boundary."""
    if kind=='region_circle':
        radius=math.dist(start,end)
        return {i for i,o in enumerate(capsules) if math.dist(start,(o.x,o.y))<=radius+1e-8}
    if kind in ('region_square','region_rect'):
        left,right=sorted((start[0],end[0]));top,bottom=sorted((start[1],end[1]))
        return {i for i,o in enumerate(capsules) if left-1e-8<=o.x<=right+1e-8 and top-1e-8<=o.y<=bottom+1e-8}
    raise ValueError('Неизвестная форма области.')

def rows(capsules,field,field_um,image_name):
    s=scale(field,field_um)
    result=[]
    for i,o in enumerate(capsules,1):
        result.append([image_name,i,round(o.x,3),round(o.y,3),round(o.diameter,3),
            round(o.major,3),round(o.minor,3),
            round(o.diameter*s,3) if s else '',round(o.major*s,3) if s else '',
            round(o.minor*s,3) if s else '',
            'ручная' if o.origin=='manual' else 'автоматическая',
            'да' if o.reviewed else 'нет',o.note])
    return result

HEADERS=['Файл','№','X (пикс.)','Y (пикс.)','Внешний диаметр (пикс.)','Большая ось (пикс.)',
    'Малая ось (пикс.)','Внешний диаметр (мкм)','Большая ось (мкм)','Малая ось (мкм)',
    'Способ','Проверено','Примечание']

def size_distribution(capsules,field,field_um,metric='diameter',bin_width=.5,reviewed_only=False,unit='um'):
    if metric not in ('diameter','major','minor') or unit not in ('um','px'):
        raise ValueError('Неизвестный размер или единица измерения.')
    if not math.isfinite(bin_width) or bin_width<=0:raise ValueError('Ширина интервала должна быть положительной.')
    factor=scale(field,field_um) if unit=='um' else 1.
    if factor is None:raise ValueError('Для размеров в мкм сначала задайте масштаб.')
    selected=[o for o in capsules if not reviewed_only or o.reviewed]
    values=np.array([getattr(o,metric)*factor for o in selected],dtype=float)
    if not len(values):return dict(values=values,edges=np.array([]),counts=np.array([],dtype=int),n=0,unreviewed=0)
    if not np.isfinite(values).all() or (values<=0).any():raise ValueError('В измерениях есть некорректные размеры.')
    left=math.floor(float(values.min())/bin_width);right=max(left+1,math.ceil(float(values.max())/bin_width))
    if right-left>400:raise ValueError('Больше 400 интервалов: увеличьте их ширину.')
    edges=np.arange(left,right+1,dtype=float)*bin_width
    if edges[-1]<values.max():edges[-1]=float(values.max())
    counts,_=np.histogram(values,bins=edges)
    if int(counts.sum())!=len(values):raise ValueError('Не все размеры попали в интервалы.')
    return dict(values=values,edges=edges,counts=counts,n=len(values),unreviewed=sum(not o.reviewed for o in selected),
        mean=float(values.mean()),median=float(np.median(values)),std=float(values.std(ddof=1)) if len(values)>1 else None,
        minimum=float(values.min()),maximum=float(values.max()))

def coverage_mask(rgb,field=None,threshold=10.,remove_grid=True,fill_interiors=True):
    """Independent photometric segmentation. Never reads capsules or detection marks."""
    if not math.isfinite(threshold) or not 1<=threshold<=100:raise ValueError('Порог должен быть от 1 до 100.')
    if has_count_marks(rgb):raise ValueError('Для площади используйте исходник без жёлтой разметки.')
    cx,cy,r=field or optical_field(rgb)
    factor=378./r
    shape=(max(1,round(rgb.shape[1]*factor)),max(1,round(rgb.shape[0]*factor)))
    if shape[0]*shape[1]>12_000_000:raise ValueError('Поле слишком мало относительно фотографии.')
    gray=cv2.resize(cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY),shape,interpolation=cv2.INTER_AREA if factor<1 else cv2.INTER_LINEAR)
    smooth=cv2.GaussianBlur(gray,(3,3),.55)
    y,x=np.indices(gray.shape)
    valid=(x-cx*factor)**2+(y-cy*factor)**2<(378.-6)**2
    # Closing estimates background without circular/elliptical object detection.
    background=cv2.morphologyEx(smooth,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(51,51)))
    residual=background.astype(float)-gray.astype(float)
    # Closing alone raises the baseline on JPEG texture. Remove that slowly
    # varying bias without deriving any boundaries from counted capsules.
    bias=cv2.medianBlur(np.clip(residual,0,255).astype('uint8'),31)
    residual-=bias.astype(float)
    binary=(residual>=threshold)&valid
    if remove_grid:
        weak=((residual>=3)&valid).astype('uint8')
        lines=np.zeros_like(weak)
        for angle in [-12,-6,0,6,12,78,84,90,96,102]:
            kernel=np.zeros((91,91),dtype='uint8');theta=math.radians(angle)
            dx,dy=44*math.cos(theta),44*math.sin(theta)
            cv2.line(kernel,(round(45-dx),round(45-dy)),(round(45+dx),round(45+dy)),1,1)
            line=cv2.morphologyEx(weak,cv2.MORPH_OPEN,kernel)
            lines|=line
        lines=cv2.dilate(lines,np.ones((3,3),'uint8'))
        binary[lines>0]=False
    binary=cv2.morphologyEx(binary.astype('uint8'),cv2.MORPH_CLOSE,np.ones((3,3),'uint8'))>0
    if fill_interiors:
        # Fill bounded interiors, not arbitrary large gaps within aggregates.
        inverse=(~binary).astype('uint8')
        count,labels,stats,_=cv2.connectedComponentsWithStats(inverse,8)
        outside=np.unique(labels[~valid])
        border=np.unique(np.concatenate([labels[0],labels[-1],labels[:,0],labels[:,-1]]))
        forbidden=set(outside.tolist()+border.tolist())
        for label in range(1,count):
            if label not in forbidden and stats[label,cv2.CC_STAT_AREA]<=650:binary[labels==label]=True
    count,labels,stats,_=cv2.connectedComponentsWithStats(binary.astype('uint8'),8)
    keep=np.zeros(count,dtype=bool)
    for label in range(1,count):keep[label]=stats[label,cv2.CC_STAT_AREA]>=3
    binary=keep[labels]&valid
    mask=cv2.resize(binary.astype('uint8'),(rgb.shape[1],rgb.shape[0]),interpolation=cv2.INTER_NEAREST)>0
    analyzed=cv2.resize(valid.astype('uint8'),(rgb.shape[1],rgb.shape[0]),interpolation=cv2.INTER_NEAREST)>0
    return mask&analyzed,analyzed

def coverage_statistics(mask,analyzed,field,field_um,reference_area_um2=None):
    if mask.shape!=analyzed.shape:raise ValueError('Маска и поле имеют разные размеры.')
    covered=int(np.count_nonzero(mask&analyzed));total=int(np.count_nonzero(analyzed))
    if not total:raise ValueError('В фотографии нет анализируемой части поля.')
    s=scale(field,field_um)
    area=covered*s*s if s else None;field_area=total*s*s if s else None
    if reference_area_um2 is not None and (not math.isfinite(reference_area_um2) or reference_area_um2<=0):
        raise ValueError('Площадь одной капсулы должна быть положительной.')
    return dict(covered_pixels=covered,field_pixels=total,coverage_percent=100*covered/total,
        area_um2=area,field_area_um2=field_area,
        estimated_count=area/reference_area_um2 if area is not None and reference_area_um2 else None,
        reference_area_um2=reference_area_um2)

def export_csv(path,capsules,field,field_um,image_name,calibration=''):
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f,delimiter=';');w.writerow(HEADERS+['Калибровка','Диаметр поля (мкм)'])
        for row in rows(capsules,field,field_um,image_name):
            # Decimal commas make this CSV work naturally in Russian Excel.
            row=[str(v).replace('.',',') if isinstance(v,float) else v for v in row]
            w.writerow(row+[calibration,str(field_um).replace('.',',') if field_um else ''])

def annotated(rgb,capsules,labels=False):
    im=Image.fromarray(rgb);draw=ImageDraw.Draw(im)
    for i,o in enumerate(capsules,1):
        color='#58f5b2' if o.reviewed else '#ffd34f' if o.uncertain else '#3de6f5'
        pts=[tuple(p) for p in o.points()];draw.line(pts+[pts[0]],fill=color,width=1)
        draw.ellipse((o.x-1,o.y-1,o.x+1,o.y+1),fill=color)
        if labels:draw.text((o.x+o.major/2+2,o.y),str(i),fill=color)
    return im

def save_project(path,rgb,capsules,field,field_um,calibration,settings,image_path):
    # Embed the image so a saved project can be moved to another computer.
    import base64,io
    buffer=io.BytesIO();Image.fromarray(rgb).save(buffer,format='PNG')
    data=dict(format='CapsuleCounter',version=VERSION,image_name=Path(image_path).name,
        image_png=base64.b64encode(buffer.getvalue()).decode('ascii'),
        field=list(field),field_um=field_um,calibration=calibration,settings=settings,
        capsules=[asdict(o) for o in capsules])
    tmp=Path(str(path)+'.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    tmp.replace(path)

def load_project(path):
    import base64,io
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('format')!='CapsuleCounter':raise ValueError('Это не проект CapsuleCounter.')
    rgb=np.array(Image.open(io.BytesIO(base64.b64decode(data['image_png']))).convert('RGB'))
    field=tuple(map(float,data['field']))
    if len(field)!=3 or not all(math.isfinite(v) for v in field) or field[2]<=0:
        raise ValueError('Некорректное поле зрения в проекте.')
    um=data.get('field_um')
    if um is not None and (not math.isfinite(um) or um<=0):raise ValueError('Некорректный масштаб.')
    objects=[Capsule(**v) for v in data['capsules']]
    for o in objects:
        if not all(math.isfinite(v) for v in [o.x,o.y,o.major,o.minor,o.angle,o.diameter]) or min(o.major,o.minor,o.diameter)<=0:
            raise ValueError('Некорректная капсула в проекте.')
    return rgb,objects,field,um,data.get('calibration',''),data.get('settings',{}),data['image_name']
