import sys
from pathlib import Path
import math
import copy
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import numpy as np
from PIL import Image, ImageTk, ImageEnhance
import engine

def configure_display():
    if sys.platform=='win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f'CapsuleCounter.Measurements.{engine.VERSION}')
        except Exception:pass

class App:
    def __init__(self,root):
        self.root=root
        root.title('Капсулы — подсчёт и измерения')
        sw,sh=root.winfo_screenwidth(),root.winfo_screenheight()
        root.geometry(f'{min(1450,sw-80)}x{min(920,sh-120)}')
        root.minsize(min(1050,sw-80),min(700,sh-120))
        self.rgb=None;self.field=None;self.objects=[];self.path='';self.selected=None
        self.histogram_window=None;self.coverage_window=None
        self.analyzed=False
        self.region=None;self.region_selected=set()
        self.undo_stack=[];self.dirty=False;self.busy=False;self.zoom=1.;self.mode=tk.StringVar(value='select')
        self.points=[];self.drag_start=None;self.drag_pos=None;self.events=queue.Queue()
        self.sensitivity=tk.IntVar(value=50);self.min_size=tk.StringVar(value='2');self.max_size=tk.StringVar(value='10')
        self.brightness=tk.StringVar(value='1.0')
        self.sharpness=tk.StringVar(value='1.0')
        self.field_um=tk.StringVar(value='275');self.um=275.;self.calibration='Предварительно: малый квадрат 50 мкм, поле ≈275 мкм'
        self.scale_text=tk.StringVar();self.stats=tk.StringVar(value='Откройте фотографию без жёлтых отметок')
        self.status=tk.StringVar(value='Фото без разметки → распознавание → проверка → экспорт')
        self.show_labels=tk.BooleanVar(value=False);self.hide_marks=tk.BooleanVar(value=False)
        style=ttk.Style();style.theme_use('clam')
        style.configure('.',font=('Segoe UI',10))
        style.configure('Title.TLabel',font=('Segoe UI',14,'bold'))
        style.configure('Accent.TButton',font=('Segoe UI',10,'bold'),padding=7)
        style.configure('Treeview', rowheight=24)
        from photo_session import PhotoSession
        self.session=PhotoSession(self)
        self.set_icon(root)
        self.build()
        root.bind('<Control-o>',lambda e:self.open_image())
        root.bind('<Control-s>',lambda e:self.save())
        root.bind('<Control-z>',lambda e:self.undo())
        root.bind('<Delete>',lambda e:self.delete())
        root.bind('<Escape>',lambda e:self.set_mode('select'))
        root.protocol('WM_DELETE_WINDOW',self.close)
        root.after(100,self.poll)
        self.update_scale()

    def build(self):
        bar=ttk.Frame(self.root,padding=10);bar.pack(fill='x')
        for text,command in [('Открыть фото',self.open_image),('Открыть проект',self.open_project),
            ('Сохранить проект',self.save),('Экспорт CSV',self.export),('Сохранить разметку',self.export_image),('Серия фото',self.batch)]:
            ttk.Button(bar,text=text,command=command).pack(side='left',padx=3)
        ttk.Button(bar,text='Инструкция',command=self.help).pack(side='right')
        analysis=ttk.Frame(self.root,padding=(10,0,10,8));analysis.pack(fill='x')
        ttk.Button(analysis,text='Распределение по размерам',command=self.show_histogram).pack(side='left',padx=3)
        ttk.Button(analysis,text='Площадь покрытия — независимый метод',command=self.show_coverage).pack(side='left',padx=3)
        ttk.Button(analysis,text='Список фотографий',command=self.session.show).pack(side='left',padx=3)
        pane=ttk.Panedwindow(self.root,orient='horizontal');pane.pack(fill='both',expand=True,padx=10)
        left_host=ttk.Frame(pane,width=280);pane.add(left_host,weight=0)
        left_scroll=tk.Canvas(left_host,width=280,highlightthickness=0,bg='#dcdad5')
        left_bar=ttk.Scrollbar(left_host,orient='vertical',command=left_scroll.yview)
        left_scroll.configure(yscrollcommand=left_bar.set)
        left_bar.pack(side='right',fill='y');left_scroll.pack(side='left',fill='both',expand=True)
        left=ttk.Frame(left_scroll,padding=10)
        left_window=left_scroll.create_window((0,0),window=left,anchor='nw')
        left.bind('<Configure>',lambda e:left_scroll.configure(scrollregion=left_scroll.bbox('all')))
        left_scroll.bind('<Configure>',lambda e:left_scroll.itemconfigure(left_window,width=e.width))
        ttk.Label(left,text='Подсчёт капсул',style='Title.TLabel').pack(anchor='w',pady=(0,8))
        ttk.Label(left,text='Ожидаемый размер, мкм').pack(anchor='w')
        r=ttk.Frame(left);r.pack(fill='x',pady=4)
        ttk.Entry(r,textvariable=self.min_size,width=8).pack(side='left')
        ttk.Label(r,text=' — ').pack(side='left')
        ttk.Entry(r,textvariable=self.max_size,width=8).pack(side='left')
        ttk.Label(left,text='Чувствительность').pack(anchor='w',pady=(10,0))
        ttk.Spinbox(left, from_=0, to=100, increment=1, textvariable=self.sensitivity).pack(fill='x')        
        ttk.Label(left,text='Яркость (можно несколько через пробел)').pack(anchor='w',pady=(10,0))
        b_spin = ttk.Spinbox(left, from_=0.1, to=3.0, increment=0.1, textvariable=self.brightness, command=self.redraw)
        b_spin.pack(fill='x')
        b_spin.bind('<Return>', lambda e: self.redraw())
        ttk.Label(left,text='Четкость (можно несколько через пробел)').pack(anchor='w',pady=(5,0))
        s_spin = ttk.Spinbox(left, from_=0.1, to=4.0, increment=0.1, textvariable=self.sharpness, command=self.redraw)
        s_spin.pack(fill='x')
        s_spin.bind('<Return>', lambda e: self.redraw())
        ttk.Label(left,text='Выше → больше отметок и ложных находок',wraplength=260).pack(anchor='w')
        self.detect_button=ttk.Button(left,text='Найти капсулы',style='Accent.TButton',command=self.detect)
        self.detect_button.pack(fill='x',pady=10)
        ttk.Separator(left).pack(fill='x',pady=7)
        ttk.Label(left,text='Масштаб',style='Title.TLabel').pack(anchor='w')
        ttk.Label(left,text='Физический диаметр поля, мкм').pack(anchor='w',pady=(6,2))
        r=ttk.Frame(left);r.pack(fill='x')
        ttk.Entry(r,textvariable=self.field_um,width=12).pack(side='left')
        ttk.Button(r,text='Применить',command=self.apply_scale).pack(side='left',padx=5)
        ttk.Label(left,textvariable=self.scale_text,wraplength=265).pack(anchor='w',pady=6)
        ttk.Button(left,text='Измерить отрезок сетки',command=lambda:self.set_mode('calibrate')).pack(fill='x')
        ttk.Label(left,text='Малый квадрат: 50 мкм. Большой: 200 мкм. Начальный масштаб приблизительный.',wraplength=265).pack(anchor='w',pady=6)
        ttk.Separator(left).pack(fill='x',pady=7)
        ttk.Label(left,text='Ручные исправления',style='Title.TLabel').pack(anchor='w')
        for value,label in [('select','Выбрать / переместить'),('circle','Круг: протянуть диаметр'),
                            ('ellipse','Эллипс: 3 точки')]:
            ttk.Radiobutton(left,text=label,variable=self.mode,value=value,command=lambda v=value:self.set_mode(v)).pack(anchor='w',pady=3)
        ttk.Label(left,text='Удаление группы отметок').pack(anchor='w',pady=(10,3))
        for value,label in [('region_circle','Выделить круглую зону'),('region_square','Выделить квадратную зону'),('region_rect','Выделить прямоугольную зону')]:
            ttk.Radiobutton(left,text=label,variable=self.mode,value=value,command=lambda v=value:self.set_mode(v)).pack(anchor='w',pady=2)
        ttk.Button(left,text='Удалить выбранные отметки',command=self.delete).pack(fill='x',pady=5)
        r=ttk.Frame(left);r.pack(fill='x',pady=7)
        ttk.Button(r,text='Удалить',command=self.delete).pack(side='left',expand=True,fill='x')
        ttk.Button(r,text='Отменить',command=self.undo).pack(side='left',expand=True,fill='x',padx=3)
        ttk.Button(left,text='Изменить размеры выбранной',command=self.edit_selected).pack(fill='x',pady=3)
        ttk.Button(left,text='Подтвердить выбранную',command=self.review_selected).pack(fill='x',pady=3)
        ttk.Button(left,text='Подтвердить все после просмотра',command=self.review_all).pack(fill='x',pady=3)
        ttk.Checkbutton(left,text='Показывать номера',variable=self.show_labels,command=self.redraw).pack(anchor='w',pady=(8,0))
        ttk.Checkbutton(left,text='Скрыть отметки',variable=self.hide_marks,command=self.redraw).pack(anchor='w')
        center=ttk.Frame(pane);pane.add(center,weight=5)
        toolbar=ttk.Frame(center);toolbar.pack(fill='x')
        ttk.Label(toolbar,textvariable=self.stats,style='Title.TLabel').pack(side='left',padx=5)
        ttk.Button(toolbar,text='Вписать',command=self.fit).pack(side='right')
        ttk.Button(toolbar,text='+',command=lambda:self.change_zoom(1.25)).pack(side='right')
        ttk.Button(toolbar,text='−',command=lambda:self.change_zoom(.8)).pack(side='right')
        cf=ttk.Frame(center);cf.pack(fill='both',expand=True)
        self.canvas=tk.Canvas(cf,bg='#101923',highlightthickness=0)
        xs=ttk.Scrollbar(cf,orient='horizontal',command=self.canvas.xview)
        ys=ttk.Scrollbar(cf,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xs.set,yscrollcommand=ys.set)
        self.canvas.grid(row=0,column=0,sticky='nsew');ys.grid(row=0,column=1,sticky='ns');xs.grid(row=1,column=0,sticky='ew')
        cf.rowconfigure(0,weight=1);cf.columnconfigure(0,weight=1)
        self.canvas.bind('<ButtonPress-1>',self.press);self.canvas.bind('<B1-Motion>',self.motion)
        self.canvas.bind('<ButtonRelease-1>',self.release)
        self.canvas.bind('<ButtonPress-2>',lambda e:self.canvas.scan_mark(e.x,e.y))
        self.canvas.bind('<B2-Motion>',lambda e:self.canvas.scan_dragto(e.x,e.y,gain=1))
        self.canvas.bind('<MouseWheel>',self.wheel)
        right=ttk.Frame(pane,width=290);pane.add(right,weight=1)
        ttk.Label(right,text='Измерения, мкм',style='Title.TLabel').pack(anchor='w',pady=5)
        self.tree=ttk.Treeview(right,columns=('d','a','b','state'),show='tree headings',selectmode='browse')
        self.tree.heading('#0',text='№');self.tree.column('#0',width=36,stretch=False)
        for col,title,w in [('d','Диам.',62),('a','Больш.',62),('b','Мал.',62),('state','✓',32)]:
            self.tree.heading(col,text=title);self.tree.column(col,width=w,anchor='center',stretch=False)
        scroll=ttk.Scrollbar(right,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.tree.pack(fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',self.table_select)
        ttk.Label(right,text='Диаметр: максимальный размер наружного контура. Автоматические размеры требуют проверки.',wraplength=265).pack(anchor='w',pady=6)
        ttk.Label(self.root,textvariable=self.status,padding=10,wraplength=1350).pack(fill='x')

    def snapshot(self):
        self.undo_stack.append((copy.deepcopy(self.objects),self.selected,self.um,self.calibration))
        self.undo_stack=self.undo_stack[-30:];self.dirty=True

    def set_icon(self,window):
        try:
            icon=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))/'assets'/'capsule.ico'
            window.iconbitmap(str(icon))
            return str(icon)
        except (tk.TclError,OSError):return None

    def show_histogram(self):
        if self.rgb is None:return
        from histogram import HistogramWindow
        if self.histogram_window and self.histogram_window.exists():
            self.histogram_window.refresh();self.histogram_window.window.lift()
        else:self.histogram_window=HistogramWindow(self)

    def show_coverage(self):
        if self.rgb is None:return
        from coverage_area import CoverageWindow
        if self.coverage_window and self.coverage_window.exists():
            self.coverage_window.calculate();self.coverage_window.window.lift()
        else:self.coverage_window=CoverageWindow(self)

    def editable(self):
        return self.rgb is not None and not self.busy

    def confirm_discard(self):
        self.session.capture()
        pending=any(r['dirty'] for r in self.session.records)
        return not (self.dirty or pending) or messagebox.askyesno('Несохранённые изменения','В списке есть несохранённые изменения. Закрыть программу без сохранения?')

    def open_image(self,path=None):
        if self.busy:return
        if path is None:
            self.session.add_files();return
        path=path or filedialog.askopenfilename(title='Фотография без разметки',filetypes=[('Изображения','*.jpg *.jpeg *.png *.tif *.tiff *.bmp'),('Все файлы','*.*')])
        if not path:return
        try:
            rgb=engine.read_image(path);field=engine.optical_field(rgb)
        except Exception as e:messagebox.showerror('Не удалось открыть',str(e));return
        self.session.capture()
        index=self.session.register(path,rgb,field)
        self.session.activate(index)

    def settings(self):
        lo=float(self.min_size.get().replace(',','.'));hi=float(self.max_size.get().replace(',','.'))
        if not math.isfinite(lo+hi) or not 0<lo<hi:raise ValueError('Нужны положительные границы: минимум меньше максимума.')
        return dict(sensitivity=int(self.sensitivity.get()),min_size=lo,max_size=hi)

    def detect(self):
        if not self.editable():return
        try:settings=self.settings()
        except ValueError as e:messagebox.showerror('Диапазон',str(e));return
        if self.objects and not messagebox.askyesno('Повторный подсчёт','Заменить текущие отметки автоматическими? Ручные исправления можно вернуть кнопкой «Отменить».'):return
        self.snapshot();self.busy=True;self.detect_button.configure(state='disabled')
        self.status.set('Распознавание…')
        rgb=self.rgb.copy();field=self.field;um=self.um
        
        import re
        b_str = re.findall(r"[\d.]+", self.brightness.get().replace(',', '.'))
        s_str = re.findall(r"[\d.]+", self.sharpness.get().replace(',', '.'))
        b_vals = [float(x) for x in b_str] if b_str else [1.0]
        s_vals = [float(x) for x in s_str] if s_str else [1.0]

        def work():
            try:
                import itertools
                from PIL import Image, ImageEnhance
                import numpy as np
                results = []
                
                for b_val, s_val in itertools.product(b_vals, s_vals):
                    im = Image.fromarray(rgb.copy())
                    if b_val != 1.0: im = ImageEnhance.Brightness(im).enhance(b_val)
                    if s_val != 1.0: im = ImageEnhance.Sharpness(im).enhance(s_val)
                    enh_rgb = np.array(im)
                    objects = engine.detect(enh_rgb, field, field_um=um, **settings)
                    results.append(objects)
                
                if len(results) > 1:
                    avg_count = sum(len(r) for r in results) / len(results)
                    best_objects = min(results, key=lambda r: abs(len(r) - avg_count))
                    self.events.put(('average_done', (avg_count, len(results))))
                    self.events.put(('done', best_objects))
                else:
                    self.events.put(('done', results[0]))
            except Exception as e:
                self.events.put(('error',str(e)))
        threading.Thread(target=work,daemon=True).start()

    def poll(self):
        try:
            kind,value=self.events.get_nowait();self.busy=False;self.detect_button.configure(state='normal')
            if kind=='batch_progress':
                self.busy=True;self.detect_button.configure(state='disabled');self.status.set(value)
            elif kind=='batch_done':
                count,errors,folder=value
                self.status.set(f'Серия: обработано {count} фото; ошибок {len(errors)}. Результаты: {folder}')
                if errors:messagebox.showwarning('Серия фото','Не обработаны:\n'+'\n'.join(errors))
            elif kind=='average_done':
                avg_count, runs = value
                messagebox.showinfo('Мульти-расчёт', f'Выполнено проходов алгоритма: {runs}\n\nСреднее количество найденных капсул: {avg_count:.1f}')
            elif kind=='done':
                self.objects=value;self.analyzed=True;self.selected=None;self.region=None;self.region_selected.clear();self.hide_marks.set(False);self.update_table();self.redraw()
                self.status.set(f'Найдено {len(value)} капсул-кандидатов. Проверьте скопления и границы; исправления сохраняются в проект.')
            else:messagebox.showerror('Распознавание',value);self.status.set('Ошибка распознавания; ручная разметка доступна.')
        except queue.Empty:pass
        self.root.after(100,self.poll)

    def batch(self):
        if self.busy:return
        paths=filedialog.askopenfilenames(title='Выберите исходные фотографии без разметки',filetypes=[('Изображения','*.jpg *.jpeg *.png *.tif *.tiff *.bmp')])
        if not paths:return
        folder=filedialog.askdirectory(title='Папка для результатов серии')
        if not folder:return
        try:settings=self.settings()
        except ValueError as e:messagebox.showerror('Диапазон',str(e));return
        um=self.um;calibration=self.calibration
        # Each batch gets a new folder; reruns never overwrite prior corrections.
        from datetime import datetime
        destination=Path(folder)/('Капсулы-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
        if destination.exists():
            import uuid
            destination=Path(str(destination)+'-'+uuid.uuid4().hex[:6])
        destination.mkdir(parents=True)
        self.busy=True;self.detect_button.configure(state='disabled');self.status.set('Обработка серии…')
        def work():
            import csv
            errors=[];summary=[];seen=set();duplicates=[]
            for i,path in enumerate(paths,1):
                self.events.put(('batch_progress',f'Серия: фото {i} из {len(paths)} — {Path(path).name}'))
                try:
                    rgb=engine.read_image(path)
                    import hashlib
                    digest=hashlib.sha256(rgb.tobytes()).hexdigest()
                    if digest in seen:
                        duplicates.append(Path(path).name);continue
                    field=engine.optical_field(rgb)
                    objects=engine.detect(rgb,field,field_um=um,**settings)
                    stem=f'{i:03d}-{Path(path).stem}'
                    engine.save_project(destination/(stem+'.capsules'),rgb,objects,field,um,calibration,settings,path)
                    engine.export_csv(destination/(stem+'.csv'),objects,field,um,Path(path).name,calibration)
                    engine.annotated(rgb,objects).save(destination/(stem+'-marked.png'))
                    summary.append([Path(path).name,len(objects),0,calibration,um])
                    seen.add(digest)
                except Exception as e:errors.append(f'{Path(path).name}: {e}')
            try:
                with open(destination/'Итоги.csv','w',encoding='utf-8-sig',newline='') as f:
                    writer=csv.writer(f,delimiter=';');writer.writerow(['Файл','Капсул-кандидатов','Проверено','Калибровка','Диаметр поля (мкм)']);writer.writerows(summary)
                if errors:(destination/'Ошибки.txt').write_text('\n'.join(errors),encoding='utf-8')
                if duplicates:(destination/'Дубликаты.txt').write_text('Точные копии фотографий пропущены:\n'+'\n'.join(duplicates),encoding='utf-8')
                self.events.put(('batch_done',(len(summary),errors,str(destination))))
            except Exception as e:self.events.put(('error',str(e)))
        threading.Thread(target=work,daemon=True).start()

    def update_scale(self):
        s=engine.scale(self.field,self.um) if self.field else None
        self.scale_text.set(self.calibration+(f'\n{s:.4f} мкм/пикс.' if s else ''))

    def apply_scale(self):
        if not self.editable():return
        try:
            v=float(self.field_um.get().replace(',','.'))
            if not math.isfinite(v) or v<=0:raise ValueError()
        except ValueError:messagebox.showerror('Масштаб','Введите положительный диаметр поля в мкм.');return
        self.snapshot();self.um=v;self.calibration='Диаметр поля введён вручную'
        self.update_scale();self.update_table();self.redraw()

    def calibrate(self,p1,p2):
        px=math.dist(p1,p2)
        if px<3:return
        length=simpledialog.askfloat('Отрезок сетки','Длина выбранного отрезка, мкм:\n50 — малый квадрат; 200 — большой.',initialvalue=50,minvalue=.001,parent=self.root)
        if length is None or not math.isfinite(length):return
        self.snapshot();self.um=length/px*2*self.field[2];self.field_um.set(f'{self.um:.4f}')
        self.calibration=f'По сетке: {length:g} мкм / {px:.2f} пикс.'
        self.update_scale();self.update_table();self.redraw()
        self.status.set('Калибровка применена. Этот физический диаметр поля используется при открытии следующих фотографий.')

    def set_mode(self,mode):
        self.region=None;self.region_selected.clear()
        if mode.startswith('region_'):self.selected=None;self.hide_marks.set(False)
        self.mode.set(mode);self.points=[];self.drag_start=None;self.drag_pos=None
        notes={'select':'Клик: выбор. Протянуть выбранную капсулу: перемещение. Delete: удалить. Колесо: прокрутка; Ctrl+колесо: масштаб.',
            'circle':'Протяните от одного края капсулы до противоположного: это диаметр.',
            'ellipse':'Кликните два конца большой оси, затем край капсулы по малой оси. Esc: выйти.',
            'calibrate':'Протяните от центра одной линии сетки до центра соседней, затем введите длину в мкм.',
            'region_circle':'Протяните от центра зоны до её края. Отметки с центрами внутри станут розовыми. Delete или «Удалить выбранные отметки»; Esc: снять выделение.',
            'region_square':'Протяните квадрат от одного угла к противоположному. Выбираются отметки с центрами внутри. Delete удаляет группу; «Отменить» возвращает её.',
            'region_rect':'Протяните прямоугольник по двум углам. Выбираются отметки с центрами внутри. Delete удаляет группу; «Отменить» возвращает её.'}
        self.status.set(notes[mode]);self.redraw()

    def pos(self,e):
        return self.canvas.canvasx(e.x)/self.zoom,self.canvas.canvasy(e.y)/self.zoom

    def valid_point(self,p):
        return self.rgb is not None and 0<=p[0]<self.rgb.shape[1] and 0<=p[1]<self.rgb.shape[0]

    def region_endpoint(self,p):
        h,w=self.rgb.shape[:2];x=min(w-1,max(0,p[0]));y=min(h-1,max(0,p[1]))
        if self.mode.get()=='region_square' and self.drag_start:
            ax,ay=self.drag_start;dx=x-ax;dy=y-ay
            sx=1 if dx>=0 else -1;sy=1 if dy>=0 else -1
            size=min(max(abs(dx),abs(dy)),w-1-ax if sx>0 else ax,h-1-ay if sy>0 else ay)
            x,y=ax+sx*size,ay+sy*size
        return x,y

    def press(self,e):
        if not self.editable():return
        p=self.pos(e)
        if not self.valid_point(p):return
        self.canvas.focus_set()
        mode=self.mode.get()
        if mode.startswith('region_'):
            self.selected=None;self.region_selected.clear();self.region=None
            self.drag_start=p;self.drag_pos=p;self.redraw();return
        self.region=None;self.region_selected.clear()
        if mode=='ellipse':
            self.points.append(p)
            if len(self.points)==3:
                a,b,c=self.points;major=math.dist(a,b)
                if major>2:
                    cx,cy=(a[0]+b[0])/2,(a[1]+b[1])/2
                    dx,dy=(b[0]-a[0])/major,(b[1]-a[1])/major
                    minor=2*abs((c[0]-cx)*(-dy)+(c[1]-cy)*dx)
                    if minor>2:
                        self.snapshot();self.objects.append(engine.Capsule(cx,cy,major,minor,math.degrees(math.atan2(dy,dx)),origin='manual',reviewed=True,uncertain=False,note='Ручная обводка'))
                        self.selected=len(self.objects)-1;self.update_table()
                self.points=[]
            self.redraw();return
        self.drag_start=p;self.drag_pos=p
        if mode=='select':
            candidates=[i for i,o in enumerate(self.objects) if o.contains(*p,tolerance=2/self.zoom)]
            self.selected=min(candidates,key=lambda i:math.dist(p,(self.objects[i].x,self.objects[i].y))) if candidates else None
            if self.selected is not None:
                self.drag_original=(self.objects[self.selected].x,self.objects[self.selected].y)
                self.moved=False;self.select_table()
            self.redraw()

    def motion(self,e):
        if not self.editable() or self.drag_start is None:return
        p=self.pos(e)
        if self.mode.get().startswith('region_'):
            self.drag_pos=self.region_endpoint(p)
            self.region_selected=engine.region_indices(self.objects,self.mode.get(),self.drag_start,self.drag_pos)
            self.status.set(f'Выбрано отметок: {len(self.region_selected)}. Delete или «Удалить выбранные отметки»; Esc: снять выделение.')
            self.redraw();return
        if not self.valid_point(p):return
        self.drag_pos=p
        if self.mode.get()=='select' and self.selected is not None:
            if math.dist(p,self.drag_start)>2/self.zoom:
                if not self.moved:self.snapshot();self.moved=True
                o=self.objects[self.selected];o.x=self.drag_original[0]+p[0]-self.drag_start[0];o.y=self.drag_original[1]+p[1]-self.drag_start[1]
                o.origin='manual';o.reviewed=False;o.uncertain=True;o.note='Перемещена вручную; проверить контур'
        self.redraw()

    def release(self,e):
        if not self.editable() or self.drag_start is None:return
        if self.mode.get().startswith('region_'):
            a=self.drag_start;b=self.region_endpoint(self.pos(e))
            self.drag_start=None;self.drag_pos=None
            if math.dist(a,b)>2/self.zoom:
                self.region=(self.mode.get(),a,b);self.region_selected=engine.region_indices(self.objects,*self.region)
            else:self.region=None;self.region_selected.clear()
            self.status.set(f'Выбрано отметок: {len(self.region_selected)}. Delete или «Удалить выбранные отметки»; «Отменить» вернёт удалённые.')
            self.redraw();return
        a,b=self.drag_start,self.drag_pos
        self.drag_start=None;self.drag_pos=None
        if self.mode.get()=='circle' and math.dist(a,b)>2:
            self.snapshot();d=math.dist(a,b)
            self.objects.append(engine.Capsule((a[0]+b[0])/2,(a[1]+b[1])/2,d,d,origin='manual',reviewed=True,uncertain=False,note='Ручная обводка'))
            self.selected=len(self.objects)-1
        elif self.mode.get()=='calibrate':self.calibrate(a,b);self.set_mode('select')
        self.update_table();self.redraw()

    def delete(self):
        if not self.editable():return
        if isinstance(self.root.focus_get(),(tk.Entry,ttk.Entry)):return
        indices=self.region_selected if self.region_selected else {self.selected} if self.selected is not None else set()
        indices={i for i in indices if 0<=i<len(self.objects)}
        if not indices:return
        self.snapshot();self.objects=[o for i,o in enumerate(self.objects) if i not in indices]
        self.selected=None;self.region_selected.clear();self.region=None;self.update_table();self.redraw()
        self.status.set(f'Удалено отметок: {len(indices)}. Фотография не изменена. «Отменить» возвращает всю группу.')

    def undo(self):
        if self.busy or not self.undo_stack:return
        self.objects,self.selected,self.um,self.calibration=self.undo_stack.pop();self.dirty=True
        self.region=None;self.region_selected.clear()
        self.field_um.set(str(self.um));self.update_scale();self.update_table();self.redraw()

    def review_selected(self):
        if not self.editable() or self.selected is None:return
        self.snapshot();self.objects[self.selected].reviewed=True;self.update_table();self.redraw()

    def review_all(self):
        if not self.editable() or not self.objects:return
        self.snapshot()
        for o in self.objects:o.reviewed=True
        self.update_table();self.redraw()

    def edit_selected(self):
        if not self.editable() or self.selected is None:return
        o=self.objects[self.selected];s=engine.scale(self.field,self.um)
        major=simpledialog.askfloat('Большая ось','Большая ось, мкм:',initialvalue=o.major*s,minvalue=.001,parent=self.root)
        if major is None:return
        minor=simpledialog.askfloat('Малая ось','Малая ось, мкм:',initialvalue=o.minor*s,minvalue=.001,parent=self.root)
        if minor is None:return
        angle=simpledialog.askfloat('Ориентация','Угол большой оси, градусы:',initialvalue=o.angle,parent=self.root)
        if angle is None or not all(math.isfinite(v) for v in [major,minor,angle]):return
        self.snapshot();self.objects[self.selected]=engine.Capsule(o.x,o.y,major/s,minor/s,angle,origin='manual',reviewed=True,uncertain=False,note='Размеры введены вручную; диаметр эллипса равен большой оси')
        self.update_table();self.redraw()

    def update_table(self):
        self.tree.delete(*self.tree.get_children())
        s=engine.scale(self.field,self.um) if self.field else None
        for i,o in enumerate(self.objects):
            vals=[f'{v*s:.2f}' for v in [o.diameter,o.major,o.minor]] if s else ['—']*3
            self.tree.insert('', 'end',iid=str(i),text=str(i+1),values=vals+['✓' if o.reviewed else '?'])
        self.stats.set(f'Капсул: {len(self.objects)}   Проверено: {sum(o.reviewed for o in self.objects)}')
        self.select_table()

        if self.histogram_window and self.histogram_window.exists():self.histogram_window.refresh()
        if self.coverage_window and self.coverage_window.exists():self.coverage_window.sync()
        self.session.refresh()

    def select_table(self):
        if self.selected is not None and self.tree.exists(str(self.selected)):
            self.tree.selection_set(str(self.selected));self.tree.see(str(self.selected))

    def table_select(self,e):
        values=self.tree.selection()
        if values:
            self.region=None;self.region_selected.clear()
            self.selected=int(values[0]);self.redraw()
            o=self.objects[self.selected];self.status.set(f'Капсула №{self.selected+1}: '+(o.note or 'Контур найден автоматически; проверьте на фото.'))

    def fit(self):
        if self.rgb is None:return
        self.root.update_idletasks();w=max(100,self.canvas.winfo_width());h=max(100,self.canvas.winfo_height())
        self.zoom=min(w/self.rgb.shape[1],h/self.rgb.shape[0]);self.canvas.xview_moveto(0);self.canvas.yview_moveto(0);self.redraw()

    def change_zoom(self,factor):
        if self.rgb is None:return
        old=self.zoom;new=max(.1,min(8,old*factor))
        x=(self.canvas.canvasx(self.canvas.winfo_width()/2))/old;y=(self.canvas.canvasy(self.canvas.winfo_height()/2))/old
        self.zoom=new;self.redraw()
        self.canvas.xview_moveto(max(0,(x*new-self.canvas.winfo_width()/2)/(self.rgb.shape[1]*new)))
        self.canvas.yview_moveto(max(0,(y*new-self.canvas.winfo_height()/2)/(self.rgb.shape[0]*new)))

    def wheel(self,e):
        if e.state & 4:self.change_zoom(1.2 if e.delta>0 else 1/1.2)
        else:self.canvas.yview_scroll(-int(e.delta/120)*3,'units')

    def redraw(self):
        self.canvas.delete('all')
        if self.rgb is None:
            self.canvas.create_text(300,160,text='Откройте фотографию капсул',fill='#cbd6e2',font=('Segoe UI',18));return
        z=self.zoom;h,w=self.rgb.shape[:2]
        import re
        try: b = float(re.findall(r"[\d.]+", self.brightness.get().replace(',', '.'))[0])
        except: b = 1.0
        try: s = float(re.findall(r"[\d.]+", self.sharpness.get().replace(',', '.'))[0])
        except: s = 1.0
        
        # Cache the resized photo; only vector annotations change while dragging.
        if getattr(self,'photo_key',None)!=(id(self.rgb), z, b, s):
            im = Image.fromarray(self.rgb)
            if b != 1.0: im = ImageEnhance.Brightness(im).enhance(b)
            if s != 1.0: im = ImageEnhance.Sharpness(im).enhance(s)
            im = im.resize((max(1,round(w*z)),max(1,round(h*z))),Image.Resampling.LANCZOS)
            self.photo=ImageTk.PhotoImage(im);self.photo_key=(id(self.rgb), z, b, s)
        self.canvas.create_image(0,0,image=self.photo,anchor='nw');self.canvas.configure(scrollregion=(0,0,w*z,h*z))
        if not self.hide_marks.get():
            for i,o in enumerate(self.objects):
                chosen=i==self.selected or i in self.region_selected
                color='#ff59bd' if chosen else '#58f5b2' if o.reviewed else '#ffd34f' if o.uncertain else '#3de6f5'
                pts=(o.points(40)*z).flatten().tolist();self.canvas.create_polygon(pts,outline=color,fill='',width=2 if chosen else 1,tags='capsule-mark')
                self.canvas.create_oval(o.x*z-1.5,o.y*z-1.5,o.x*z+1.5,o.y*z+1.5,fill=color,outline='')
                if self.show_labels.get():self.canvas.create_text((o.x+o.major/2)*z+3,o.y*z,text=str(i+1),fill=color,anchor='w',font=('Segoe UI',9))
        for x,y in self.points:self.canvas.create_oval(x*z-3,y*z-3,x*z+3,y*z+3,outline='#ff59bd',width=2)
        if len(self.points)==2:self.canvas.create_line(*(np.array(self.points)*z).flatten().tolist(),fill='#ff59bd',width=2)
        area=(self.mode.get(),self.drag_start,self.drag_pos) if self.drag_start and self.mode.get().startswith('region_') else self.region
        if area:
            kind,a,b=area
            if kind=='region_circle':
                radius=math.dist(a,b)
                self.canvas.create_oval((a[0]-radius)*z,(a[1]-radius)*z,(a[0]+radius)*z,(a[1]+radius)*z,outline='#ff59bd',width=2,dash=(6,3),tags='selection-area')
            else:self.canvas.create_rectangle(a[0]*z,a[1]*z,b[0]*z,b[1]*z,outline='#ff59bd',width=2,dash=(6,3),tags='selection-area')
        if self.drag_start and self.drag_pos and self.mode.get() in ['circle','calibrate']:
            a,b=self.drag_start,self.drag_pos
            self.canvas.create_line(a[0]*z,a[1]*z,b[0]*z,b[1]*z,fill='#ff59bd',width=2)
            if self.mode.get()=='circle':
                r=math.dist(a,b)/2;x=(a[0]+b[0])/2;y=(a[1]+b[1])/2
                self.canvas.create_oval((x-r)*z,(y-r)*z,(x+r)*z,(y+r)*z,outline='#ff59bd',width=2)

    def save(self,path=None):
        if not self.editable():return
        path=path or filedialog.asksaveasfilename(defaultextension='.capsules',filetypes=[('Проект капсул','*.capsules')],initialfile=Path(self.path).stem+'.capsules')
        if not path:return
        try:engine.save_project(path,self.rgb,self.objects,self.field,self.um,self.calibration,self.settings(),self.path)
        except Exception as e:messagebox.showerror('Сохранение',str(e));return
        self.dirty=False;self.status.set('Проект сохранён вместе с фотографией, масштабом и ручными исправлениями.')

    def open_project(self,path=None):
        if self.busy:return
        path=path or filedialog.askopenfilename(filetypes=[('Проект капсул','*.capsules')])
        if not path:return
        try:rgb,objects,field,um,calibration,settings,name=engine.load_project(path)
        except Exception as e:messagebox.showerror('Проект',str(e));return
        self.session.capture()
        index=self.session.register(path,rgb,field,objects,um,calibration,settings,analyzed=settings.get('measurement_done',True))
        self.session.records[index]['path']=name
        self.session.activate(index)
        self.status.set('Проект открыт. Все отметки и размеры восстановлены.')

    def export(self,path=None):
        if not self.editable():return
        path=path or filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('Таблица для Excel','*.csv')],initialfile=Path(self.path).stem+'-capsules.csv')
        if not path:return
        try:engine.export_csv(path,self.objects,self.field,self.um,Path(self.path).name,self.calibration)
        except Exception as e:messagebox.showerror('Экспорт',str(e));return
        self.status.set(f'В таблицу экспортировано {len(self.objects)} капсул. В ней указан масштаб и статус проверки.')

    def export_image(self,path=None):
        if not self.editable():return
        path=path or filedialog.asksaveasfilename(defaultextension='.png',filetypes=[('PNG','*.png')],initialfile=Path(self.path).stem+'-marked.png')
        if not path:return
        try:engine.annotated(self.rgb,self.objects,labels=self.show_labels.get()).save(path)
        except Exception as e:messagebox.showerror('Разметка',str(e));return
        self.status.set('Изображение с отметками сохранено.')

    def help(self):
        import instructions
        instructions.show(self)

    def close(self):
        if self.busy:
            if not messagebox.askyesno('Распознавание','Закрыть программу во время обработки?'):return
        if self.confirm_discard():self.root.destroy()

def main():
    configure_display()
    root=tk.Tk();app=App(root)
    if '--smoke-test' in sys.argv:
        index=sys.argv.index('--smoke-test')
        if index+1<len(sys.argv):app.open_project(sys.argv[index+1])
        root.after(2500,root.destroy)
    elif len(sys.argv)>1:
        p=sys.argv[1]
        if p.endswith('.capsules'):app.open_project(p)
        else:app.open_image(p)
    root.mainloop()

def verify_package(project_folder,report_path):
    """Exercise the actual frozen executable, its GUI and OpenCV dependencies."""
    import json
    configure_display()
    root=tk.Tk();root.withdraw();gui=App(root);root.update()
    result={'version':engine.VERSION,'frozen':bool(getattr(sys,'frozen',False)),'images':[],'gui_checks':[]}
    try:
        for path in sorted(Path(project_folder).glob('photo-*.capsules')):
            rgb,objects,field,um,calibration,settings,name=engine.load_project(path)
            found=engine.detect(rgb,field,field_um=um,**settings)
            if len(found)!=len(objects):raise AssertionError(f'Changed result: {path.name}')
            result['images'].append({'project':path.name,'count':len(found)})
        first=next(Path(project_folder).glob('photo-*.capsules'))
        gui.open_project(str(first));root.update();n=len(gui.objects)
        gui.selected=0;gui.delete();assert len(gui.objects)==n-1
        gui.undo();assert len(gui.objects)==n
        gui.review_selected();assert gui.objects[0].reviewed
        gui.undo();assert not gui.objects[0].reviewed
        gui.field_um.set('300');gui.apply_scale();assert gui.um==300
        gui.undo();assert gui.um==275
        engine.export_csv(Path(report_path).parent/'packaged-test.csv',gui.objects,gui.field,gui.um,gui.path,gui.calibration)
        engine.save_project(Path(report_path).parent/'packaged-test.capsules',gui.rgb,gui.objects,gui.field,gui.um,gui.calibration,gui.settings(),gui.path)
        loaded=engine.load_project(Path(report_path).parent/'packaged-test.capsules')
        assert len(loaded[1])==n
        result['gui_checks']=['open project','delete/undo','review/undo','scale/undo','CSV export','project save/load']
        gui.show_histogram();root.update()
        histogram=gui.histogram_window
        assert histogram.result['n']==n and int(histogram.result['counts'].sum())==n
        histogram.save_plot(Path(report_path).parent/'packaged-histogram.png')
        histogram.save_csv(Path(report_path).parent/'packaged-histogram.csv')
        gui.show_coverage();root.update()
        coverage=gui.coverage_window
        assert coverage.result and coverage.result['estimated_count'] is None
        mask=coverage.mask.copy();area=coverage.result['area_um2']
        old_objects=gui.objects;gui.objects=[];gui.update_table();coverage.calculate()
        assert np.array_equal(mask,coverage.mask) and coverage.result['area_um2']==area
        gui.objects=old_objects;gui.update_table()
        coverage.reference.set('10');coverage.statistics()
        assert abs(coverage.result['estimated_count']-area/10)<1e-8
        coverage.save_mask(Path(report_path).parent/'packaged-area-mask.png')
        coverage.save_csv(Path(report_path).parent/'packaged-area.csv')
        result['gui_checks']+=['histogram and PNG/CSV exports','independent area ignores capsule list','area reference and mask/CSV exports']
        gui.session.capture();first_index=gui.session.active
        gui.selected=0;gui.delete();assert len(gui.objects)==n-1
        second=sorted(Path(project_folder).glob('photo-*.capsules'))[1]
        gui.open_project(str(second));second_index=gui.session.active
        gui.session.activate(first_index);assert len(gui.objects)==n-1
        gui.undo();assert len(gui.objects)==n
        gui.session.activate(second_index);gui.session.show();root.update()
        gui.help();root.update();assert gui.instructions_window.winfo_exists()
        assert gui.set_icon(root)
        import ctypes,zipfile,xml.etree.ElementTree as ET
        hwnd=ctypes.windll.user32.GetParent(root.winfo_id())
        assert ctypes.windll.user32.SendMessageW(hwnd,0x7f,0,0)
        destination=gui.session.export(Path(report_path).parent)
        with zipfile.ZipFile(destination/'Результаты.xlsx') as report:
            assert report.testzip() is None
            assert len([name for name in report.namelist() if name.startswith('xl/media/')])==2
            for name in report.namelist():
                if name.endswith('.xml') or name.endswith('.rels'):ET.fromstring(report.read(name))
        result['gui_checks']+=['multiple-photo switch preserves edits and undo','instructions tabs','Windows window icon','combined XLSX embeds two marked photos']
        previous=copy.deepcopy(gui.objects);gui.set_mode('region_rect')
        gui.region=('region_rect',(0,0),(gui.rgb.shape[1],gui.rgb.shape[0]))
        gui.region_selected=engine.region_indices(gui.objects,*gui.region)
        assert len(gui.region_selected)==len(previous)
        gui.delete();assert not gui.objects
        gui.undo();assert len(gui.objects)==len(previous)
        assert [(o.x,o.y,o.major,o.minor) for o in gui.objects]==[(o.x,o.y,o.major,o.minor) for o in previous]
        result['gui_checks']+=['group deletion and one-step restoration']
        result['pass']=True
    except Exception as e:
        import traceback
        result['pass']=False;result['error']=traceback.format_exc()
    finally:root.destroy()
    Path(report_path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['pass'] else 1

if __name__=='__main__':
    if '--verify-package' in sys.argv:
        i=sys.argv.index('--verify-package')
        sys.exit(verify_package(sys.argv[i+1],sys.argv[i+2]))
    try:main()
    except Exception:
        import traceback
        report=traceback.format_exc()
        try:messagebox.showerror('Ошибка запуска',report)
        except Exception:pass
        raise
