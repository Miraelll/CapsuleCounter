"""Independent area analysis: this module does not read the capsule list."""
from pathlib import Path
import csv
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import numpy as np
import cv2
from PIL import Image,ImageTk
import engine

class CoverageWindow:
    def __init__(self,app):
        self.app=app;self.mask=None;self.analyzed=None;self.result=None;self.image=None
        self.reference_pixels=None;self.sample_mode=False;self.start=None;self.zoom=1.
        self.window=tk.Toplevel(app.root);self.window.title('Независимая площадь покрытия')
        app.set_icon(self.window)
        self.window.geometry(f'{min(1100,self.window.winfo_screenwidth()-100)}x{min(800,self.window.winfo_screenheight()-140)}')
        self.threshold=tk.StringVar(value='10');self.grid=tk.BooleanVar(value=True);self.fill=tk.BooleanVar(value=True)
        self.reference=tk.StringVar();self.summary=tk.StringVar();self.note=tk.StringVar()
        self.view=tk.StringVar(value='Наложение')
        top=ttk.Frame(self.window,padding=10);top.pack(fill='x')
        ttk.Label(top,text='Порог контраста (1–100)').pack(side='left')
        ttk.Entry(top,textvariable=self.threshold,width=6).pack(side='left',padx=5)
        ttk.Checkbutton(top,text='Убирать сетку',variable=self.grid).pack(side='left',padx=5)
        ttk.Checkbutton(top,text='Заполнять замкнутые капсулы',variable=self.fill).pack(side='left',padx=5)
        ttk.Button(top,text='Рассчитать площадь',command=self.calculate).pack(side='left',padx=5)
        ttk.Label(self.window,textvariable=self.summary,padding=10,wraplength=1000).pack(fill='x')
        row=ttk.Frame(self.window,padding=(10,0));row.pack(fill='x')
        ttk.Label(row,text='Площадь одной капсулы, мкм²:').pack(side='left')
        reference_entry=ttk.Entry(row,textvariable=self.reference,width=12);reference_entry.pack(side='left',padx=5)
        reference_entry.bind('<KeyRelease>',lambda e:setattr(self,'reference_pixels',None))
        ttk.Button(row,text='Пересчитать количество',command=self.statistics).pack(side='left',padx=5)
        ttk.Button(row,text='Выбрать одиночную капсулу рамкой',command=self.sample).pack(side='left',padx=5)
        row=ttk.Frame(self.window,padding=8);row.pack(fill='x')
        combo=ttk.Combobox(row,textvariable=self.view,values=['Наложение','Маска','Исходник'],state='readonly',width=15)
        combo.pack(side='left');combo.bind('<<ComboboxSelected>>',lambda e:self.draw())
        ttk.Button(row,text='+',command=lambda:self.resize(1.5)).pack(side='left',padx=4)
        ttk.Button(row,text='−',command=lambda:self.resize(1/1.5)).pack(side='left')
        ttk.Label(row,text='Красный цвет — выделенная площадь. Серая область вне анализа.').pack(side='left',padx=10)
        frame=ttk.Frame(self.window);frame.pack(fill='both',expand=True,padx=10)
        self.canvas=tk.Canvas(frame,bg='#101923',highlightthickness=0)
        x=ttk.Scrollbar(frame,orient='horizontal',command=self.canvas.xview)
        y=ttk.Scrollbar(frame,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x.set,yscrollcommand=y.set)
        self.canvas.grid(row=0,column=0,sticky='nsew');x.grid(row=1,column=0,sticky='ew');y.grid(row=0,column=1,sticky='ns')
        frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
        self.canvas.bind('<ButtonPress-1>',self.press);self.canvas.bind('<B1-Motion>',self.motion);self.canvas.bind('<ButtonRelease-1>',self.release)
        bottom=ttk.Frame(self.window,padding=8);bottom.pack(fill='x')
        ttk.Button(bottom,text='Сохранить маску PNG',command=self.save_mask).pack(side='left',padx=4)
        ttk.Button(bottom,text='Экспорт площади CSV',command=self.save_csv).pack(side='left',padx=4)
        ttk.Label(self.window,textvariable=self.note,padding=8,wraplength=1000).pack(fill='x')
        self.window.update_idletasks();self.calculate()

    def exists(self):return bool(self.window.winfo_exists())

    def sync(self):
        if self.image is not self.app.rgb:self.calculate()
        else:self.statistics()

    def calculate(self):
        try:
            new=self.image is not self.app.rgb
            params=(float(self.threshold.get().replace(',','.')),self.grid.get(),self.fill.get())
            mask,analyzed=engine.coverage_mask(self.app.rgb,self.app.field,*params)
            if new or params!=getattr(self,'parameters',None):
                self.reference_pixels=None;self.reference.set('')
            self.mask,self.analyzed=mask,analyzed;self.image=self.app.rgb;self.parameters=params
            if new:
                self.zoom=min(max(self.canvas.winfo_width(),400)/self.image.shape[1],max(self.canvas.winfo_height(),300)/self.image.shape[0])
            self.note.set('Метод: локальный фон → контраст → удаление сетки → заполнение замкнутых областей. Контуры подсчёта не используются. Площадь — видимая проекция; перекрытия учитываются один раз. Проверьте маску и порог. На обрезанном фото процент относится к видимой части поля; край оптики исключён.')
            self.statistics();self.draw()
        except Exception as e:messagebox.showerror('Площадь',str(e),parent=self.window)

    def statistics(self):
        if self.mask is None:return
        try:
            s=engine.scale(self.app.field,self.app.um)
            if self.reference_pixels is not None:
                area=self.reference_pixels*s*s if s else None
                self.reference.set(f'{area:.6f}' if area else '')
            text=self.reference.get().strip();reference=float(text.replace(',','.')) if text else None
            self.result=engine.coverage_statistics(self.mask,self.analyzed,self.app.field,self.app.um,reference)
            r=self.result
            area=f"{r['area_um2']:.2f} мкм²" if r['area_um2'] is not None else f"{r['covered_pixels']} пикселей² (задайте масштаб)"
            count=f"≈ {r['estimated_count']:.1f} капсул по площади; перекрытия занижают оценку" if r['estimated_count'] is not None else 'Количество по площади: выберите образец или задайте площадь одной капсулы'
            self.summary.set(f"Покрыто: {area}   |   {r['coverage_percent']:.2f}% анализируемого поля\n{count}\nМасштаб: {self.app.calibration}")
        except Exception as e:
            self.result=None;self.summary.set(str(e))

    def rendered(self):
        if self.view.get()=='Маска':return Image.fromarray((self.mask*255).astype('uint8')).convert('RGB')
        pixels=self.image.copy()
        if self.view.get()=='Наложение':
            pixels[~self.analyzed]=(pixels[~self.analyzed].astype(float)*.35).astype('uint8')
            pixels[self.mask]=(pixels[self.mask].astype(float)*.4+np.array([255,55,55])*.6).astype('uint8')
        return Image.fromarray(pixels)

    def draw(self):
        if self.mask is None:return
        im=self.rendered();w,h=im.size;self.tkimage=ImageTk.PhotoImage(im.resize((max(1,round(w*self.zoom)),max(1,round(h*self.zoom)))))
        self.canvas.delete('all');self.canvas.create_image(0,0,anchor='nw',image=self.tkimage)
        self.canvas.configure(scrollregion=(0,0,w*self.zoom,h*self.zoom))

    def resize(self,factor):self.zoom=max(.1,min(8,self.zoom*factor));self.draw()
    def sample(self):
        self.sample_mode=True;self.note.set('Увеличьте изображение и обведите рамкой одну изолированную капсулу целиком. Её площадь будет измерена по независимой маске.')
    def point(self,e):return (self.canvas.canvasx(e.x)/self.zoom,self.canvas.canvasy(e.y)/self.zoom)
    def press(self,e):
        if self.sample_mode:self.start=self.point(e)
        else:self.canvas.scan_mark(e.x,e.y)
    def motion(self,e):
        if self.sample_mode and self.start:
            p=self.point(e);self.canvas.delete('sample');self.canvas.create_rectangle(*(v*self.zoom for v in (*self.start,*p)),outline='#50f5bc',width=2,tags='sample')
        elif not self.sample_mode:self.canvas.scan_dragto(e.x,e.y,gain=1)
    def release(self,e):
        if not self.sample_mode or self.start is None:return
        a=self.start;b=self.point(e);self.sample_mode=False;self.start=None
        try:self.measure_sample(a,b)
        except ValueError as error:messagebox.showerror('Образец',str(error),parent=self.window)

    def measure_sample(self,a,b):
        h,w=self.mask.shape
        x0,x1=sorted([int(a[0]),int(b[0])]);y0,y1=sorted([int(a[1]),int(b[1])])
        x0=max(0,x0);y0=max(0,y0);x1=min(w,x1);y1=min(h,y1)
        if x1-x0<3 or y1-y0<3:raise ValueError('Выберите рамкой целую одиночную капсулу.')
        crop=self.mask[y0:y1,x0:x1].astype('uint8')
        n,labels,stats,_=cv2.connectedComponentsWithStats(crop,8)
        candidates=[i for i in range(1,n) if stats[i,cv2.CC_STAT_AREA]>=3]
        if len(candidates)!=1:raise ValueError('В рамке должна быть одна связная капсула на маске. Выберите изолированный образец и проверьте порог.')
        i=candidates[0]
        if i in np.unique(np.concatenate([labels[0],labels[-1],labels[:,0],labels[:,-1]])):
            raise ValueError('Капсула касается рамки. Выберите её целиком с запасом.')
        self.reference_pixels=int(stats[i,cv2.CC_STAT_AREA]);self.statistics()
        self.note.set('Площадь образца измерена по независимой маске. Один образец даёт приблизительное количество; выбирайте типичную одиночную капсулу.')

    def save_mask(self,path=None):
        if self.mask is None:return
        path=path or filedialog.asksaveasfilename(parent=self.window,defaultextension='.png',initialfile=Path(self.app.path).stem+'-area-mask.png')
        if path:Image.fromarray((self.mask*255).astype('uint8')).save(path)

    def save_csv(self,path=None):
        self.statistics()
        if self.result is None:return
        path=path or filedialog.asksaveasfilename(parent=self.window,defaultextension='.csv',initialfile=Path(self.app.path).stem+'-area.csv')
        if not path:return
        data={'Файл':self.app.path,'Метод':'Независимая сегментация пикселей','Порог':self.parameters[0],
            'Удаление сетки':self.parameters[1],'Заполнение интерьеров':self.parameters[2],
            'Калибровка':self.app.calibration,'Диаметр поля (мкм)':self.app.um,
            'Площадь покрытия (мкм²)':self.result['area_um2'],'Покрытие (%)':self.result['coverage_percent'],
            'Площадь анализируемого поля (мкм²)':self.result['field_area_um2'],
            'Площадь одной капсулы (мкм²)':self.result['reference_area_um2'],
            'Приблизительное количество по площади':self.result['estimated_count'],
            'Образец измерен по маске':self.reference_pixels is not None,
            'Покрытые пиксели':self.result['covered_pixels'],'Пиксели анализируемого поля':self.result['field_pixels'],
            'Ограничение':'Перекрытия считаются один раз; процент относится к видимой части поля без оптического края'}
        with open(path,'w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f,delimiter=';');writer.writerow(['Показатель','Значение'])
            writer.writerows((k,'' if v is None else str(v).replace('.',',') if isinstance(v,float) else v) for k,v in data.items())
