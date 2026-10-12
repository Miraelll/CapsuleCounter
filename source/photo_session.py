from pathlib import Path
import copy
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import engine

class PhotoSession:
    def __init__(self,app):
        self.app=app;self.records=[];self.active=None;self.window=None

    def capture(self):
        if self.active is None or self.app.rgb is None:return
        a=self.app;r=self.records[self.active]
        r.update(rgb=a.rgb,field=a.field,objects=copy.deepcopy(a.objects),um=a.um,calibration=a.calibration,
            path=a.path,undo=copy.deepcopy(a.undo_stack),selected=a.selected,dirty=a.dirty,
            analyzed=a.analyzed,settings_raw=(a.sensitivity.get(),a.min_size.get(),a.max_size.get()),coverage=None)
        try:r['settings']=a.settings()
        except ValueError:pass
        c=a.coverage_window
        if c and c.exists() and c.image is a.rgb and c.result:
            r['coverage']=dict(statistics=copy.deepcopy(c.result),parameters=c.parameters,
                reference_pixels=c.reference_pixels,reference=c.reference.get())
        self.refresh()

    def register(self,path,rgb=None,field=None,objects=None,um=None,calibration=None,settings=None,analyzed=False):
        key=str(Path(path).resolve()).casefold()
        for i,r in enumerate(self.records):
            if r['key']==key:return i
        a=self.app
        settings=settings or dict(sensitivity=50,min_size=2,max_size=10)
        r=dict(key=key,path=str(path),rgb=rgb,field=field,objects=objects or [],um=a.um if um is None else um,
            calibration=calibration or a.calibration,settings=settings,
            settings_raw=(settings['sensitivity'],str(settings['min_size']),str(settings['max_size'])),
            analyzed=analyzed,dirty=False,undo=[],selected=None,coverage=None)
        self.records.append(r);self.refresh();return len(self.records)-1

    def activate(self,index):
        a=self.app
        if a.busy:return False
        if index==self.active:return True
        self.capture();r=self.records[index]
        try:
            if r['rgb'] is None:
                r['rgb']=engine.read_image(r['path']);r['field']=engine.optical_field(r['rgb'])
        except Exception as error:
            messagebox.showerror('Фотография',f"{r['path']}\n{error}",parent=self.window or a.root);return False
        self.active=index
        a.rgb,a.field,a.path=r['rgb'],r['field'],r['path']
        a.objects=copy.deepcopy(r['objects']);a.um=r['um'];a.calibration=r['calibration'];a.analyzed=r['analyzed']
        a.undo_stack=copy.deepcopy(r['undo']);a.selected=r['selected'];a.dirty=r['dirty'];a.points=[]
        a.drag_start=None;a.drag_pos=None;a.field_um.set('' if a.um is None else str(a.um))
        a.region=None;a.region_selected.clear()
        sensitivity,lo,hi=r['settings_raw'];a.sensitivity.set(sensitivity);a.min_size.set(lo);a.max_size.set(hi)
        a.update_scale();a.update_table();a.root.update_idletasks();a.fit()
        c=a.coverage_window
        if c and c.exists() and r.get('coverage'):
            data=r['coverage'];threshold,grid,fill=data['parameters']
            c.threshold.set(str(threshold));c.grid.set(grid);c.fill.set(fill);c.calculate()
            c.reference_pixels=data['reference_pixels'];c.reference.set(data['reference']);c.statistics()
        self.refresh();a.status.set(f"Открыто: {Path(r['path']).name}. Разметка остальных фотографий хранится в списке.")
        return True

    def add_files(self,paths=None):
        if self.app.busy:return
        paths=paths or filedialog.askopenfilenames(parent=self.window or self.app.root,title='Добавить фотографии',
            filetypes=[('Изображения','*.jpg *.jpeg *.png *.tif *.tiff *.bmp')])
        if not paths:return
        self.capture();indices=[self.register(p) for p in paths]
        if self.active is None:self.activate(indices[0])
        self.show();self.refresh()

    def show(self):
        if self.window and self.window.winfo_exists():self.window.lift();self.refresh();return
        self.window=tk.Toplevel(self.app.root);self.window.title('Фотографии капсул')
        self.window.geometry('1100x560');self.window.minsize(750,360);self.app.set_icon(self.window)       
        top=ttk.Frame(self.window,padding=10);top.pack(fill='x')
        ttk.Button(top,text='Добавить фотографии',command=self.add_files).pack(side='left',padx=4)
        ttk.Button(top,text='Открыть выбранную',command=self.open_selected).pack(side='left',padx=4)
        ttk.Button(top,text='Сохранить всё: Excel и картинки',command=self.export).pack(side='left',padx=4)
        host=ttk.Frame(self.window,padding=10);host.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(host,columns=('name','count','review','state'),show='headings',selectmode='browse')
        for key,label,width in [('name','Фотография',550),('count','Капсул',90),('review','Проверено',100),('state','Состояние',160)]:
            self.tree.heading(key,text=label);self.tree.column(key,width=width,stretch=key=='name')
        scroll=ttk.Scrollbar(host,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.tree.pack(fill='both',expand=True)
        self.tree.bind('<Double-1>',lambda e:self.open_selected());self.tree.bind('<Return>',lambda e:self.open_selected())
        ttk.Label(self.window,text='Выберите файл и нажмите «Открыть выбранную» или щёлкните дважды. Измерения и отмена сохраняются отдельно для каждой фотографии. Для сохранения на диск используйте экспорт или проект.',wraplength=900,padding=10).pack(fill='x')
        self.refresh()

    def refresh(self):
        if not self.window or not self.window.winfo_exists():return
        selection=self.tree.selection();self.tree.delete(*self.tree.get_children())
        for i,r in enumerate(self.records):
            current=i==self.active;a=self.app
            objects=a.objects if current else r['objects']
            done=(a.analyzed or a.dirty or bool(objects)) if current else (r['analyzed'] or r['dirty'] or bool(objects))
            self.tree.insert('', 'end',iid=str(i),values=(Path(r['path']).name,len(objects) if done else '—',
                sum(o.reviewed for o in objects) if done else '—','Открыта' if current else 'Есть измерения' if done else 'Не измерена'))
        selected=selection[0] if selection and self.tree.exists(selection[0]) else str(self.active) if self.active is not None else None
        if selected is not None:self.tree.selection_set(selected)

    def open_selected(self):
        selection=self.tree.selection()
        if selection:self.activate(int(selection[0]))

    def export(self,folder=None):
        if self.app.busy:return
        self.capture()
        if not self.records:return
        folder=folder or filedialog.askdirectory(parent=self.window or self.app.root,title='Папка для Excel и отмеченных фотографий')
        if not folder:return
        from report_export import export_session
        try:
            destination=export_session(self.records,folder)
            for record in self.records:record['dirty']=False
            self.app.dirty=False;self.refresh()
            self.app.status.set(f'Сохранены общий Excel со встроенными картинками, изображения и проекты: {destination}')
            return destination
        except Exception as error:messagebox.showerror('Сохранение результатов',str(error),parent=self.window or self.app.root)
