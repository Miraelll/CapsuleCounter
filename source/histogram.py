from pathlib import Path
import csv
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.ticker import MaxNLocator
import engine

METRICS={'Внешний диаметр':'diameter','Большая ось':'major','Малая ось':'minor'}
UNITS={'мкм':'um','пиксели':'px'}

class HistogramWindow:
    def __init__(self,app):
        self.app=app;self.result=None
        self.window=tk.Toplevel(app.root);self.window.title('Распределение по размерам')
        app.set_icon(self.window)
        self.window.geometry(f'{min(1060,self.window.winfo_screenwidth()-100)}x{min(760,self.window.winfo_screenheight()-140)}')
        self.window.minsize(760,500)
        self.metric=tk.StringVar(value='Внешний диаметр');self.unit=tk.StringVar(value='мкм')
        self.width=tk.StringVar(value='0,5');self.population=tk.StringVar(value='Все капсулы')
        self.summary=tk.StringVar();self.note=tk.StringVar()
        top=ttk.Frame(self.window,padding=10);top.pack(fill='x')
        for label,var,values,width in [('Размер',self.metric,list(METRICS),19),('Единицы',self.unit,list(UNITS),8),
            ('Выборка',self.population,['Все капсулы','Только проверенные'],20)]:
            group=ttk.Frame(top);group.pack(side='left',padx=4);ttk.Label(group,text=label).pack(anchor='w')
            combo=ttk.Combobox(group,textvariable=var,values=values,state='readonly',width=width)
            combo.pack();combo.bind('<<ComboboxSelected>>',lambda e:self.refresh())
        group=ttk.Frame(top);group.pack(side='left',padx=4);ttk.Label(group,text='Ширина интервала').pack(anchor='w')
        entry=ttk.Entry(group,textvariable=self.width,width=10);entry.pack();entry.bind('<Return>',lambda e:self.refresh())
        ttk.Button(top,text='Построить',command=self.refresh).pack(side='left',padx=7,pady=(15,0))
        ttk.Label(self.window,textvariable=self.summary,padding=(15,4),wraplength=1000).pack(fill='x')
        self.figure=Figure(figsize=(9,4.8),dpi=100,layout='constrained');self.ax=self.figure.add_subplot(111)
        self.canvas=FigureCanvasTkAgg(self.figure,master=self.window);self.canvas.get_tk_widget().pack(fill='both',expand=True,padx=10)
        bottom=ttk.Frame(self.window,padding=10);bottom.pack(fill='x')
        ttk.Button(bottom,text='Сохранить график',command=self.save_plot).pack(side='left',padx=4)
        ttk.Button(bottom,text='Экспорт интервалов CSV',command=self.save_csv).pack(side='left',padx=4)
        ttk.Button(bottom,text='Закрыть',command=self.window.destroy).pack(side='right')
        ttk.Label(self.window,textvariable=self.note,padding=(15,5),wraplength=1000).pack(fill='x')
        self.refresh()

    def exists(self):
        try:return bool(self.window.winfo_exists())
        except tk.TclError:return False

    def refresh(self):
        if not self.exists():return
        self.result=None;self.ax.clear()
        try:
            r=engine.size_distribution(self.app.objects,self.app.field,self.app.um,METRICS[self.metric.get()],
                float(self.width.get().replace(',','.')),self.population.get()=='Только проверенные',UNITS[self.unit.get()])
        except (ValueError,TypeError) as e:
            self.summary.set(str(e));self.note.set('Исправьте настройки и нажмите «Построить».')
            self.ax.text(.5,.5,'Ошибка настроек',ha='center',va='center',transform=self.ax.transAxes);self.canvas.draw_idle();return
        self.result=r
        if not r['n']:
            self.summary.set('В выбранной выборке нет капсул.');self.note.set('Проверьте выборку и подтвердите нужные капсулы.')
            self.ax.text(.5,.5,'Нет измерений',ha='center',va='center',transform=self.ax.transAxes);self.canvas.draw_idle();return
        edges=r['edges'];counts=r['counts']
        self.ax.bar(edges[:-1],counts,width=edges[1:]-edges[:-1],align='edge',color='#268ba4',edgecolor='white',linewidth=.8)
        self.ax.set_xlabel(f'{self.metric.get()}, {self.unit.get()}');self.ax.set_ylabel('Количество капсул')
        self.ax.set_title(f'Распределение по размерам — {Path(self.app.path).name}',fontsize=11)
        self.ax.set_xlim(edges[0],edges[-1]);self.ax.set_ylim(0,max(1,int(max(counts))*1.16))
        self.ax.yaxis.set_major_locator(MaxNLocator(integer=True));self.ax.xaxis.set_major_locator(MaxNLocator(nbins=12))
        self.ax.grid(axis='y',alpha=.22);self.ax.set_axisbelow(True);self.ax.spines[['top','right']].set_visible(False)
        sd=f"{r['std']:.2f}" if r['std'] is not None else '—'
        self.summary.set(f"Капсул: {r['n']}    Среднее: {r['mean']:.2f}    Медиана: {r['median']:.2f}    "
            f"Станд. отклонение: {sd}    Мин.–макс.: {r['minimum']:.2f}–{r['maximum']:.2f} {self.unit.get()}")
        self.note.set((f'Масштаб: {self.app.calibration}. ' if self.unit.get()=='мкм' else '')+
            f"Непроверенных отметок: {r['unreviewed']}. Последний интервал включает правую границу.")
        self.canvas.draw_idle()

    def save_plot(self,path=None):
        self.refresh()
        if not self.result or not self.result['n']:return
        path=path or filedialog.asksaveasfilename(parent=self.window,defaultextension='.png',
            initialfile=Path(self.app.path).stem+'-histogram.png',filetypes=[('PNG','*.png'),('SVG','*.svg'),('PDF','*.pdf')])
        if not path:return
        caption=None
        try:
            caption=self.figure.text(.5,-.065,self.population.get()+f"; N={self.result['n']}; "+
                (self.app.calibration if self.unit.get()=='мкм' else 'Размеры в пикселях'),ha='center',fontsize=8,wrap=True)
            self.figure.savefig(path,dpi=200,bbox_inches='tight',facecolor='white')
        except Exception as e:messagebox.showerror('График',str(e),parent=self.window)
        finally:
            if caption:caption.remove()

    def save_csv(self,path=None):
        self.refresh()
        if not self.result or not self.result['n']:return
        path=path or filedialog.asksaveasfilename(parent=self.window,defaultextension='.csv',
            initialfile=Path(self.app.path).stem+'-histogram.csv',filetypes=[('CSV для Excel','*.csv')])
        if not path:return
        try:
            r=self.result
            with open(path,'w',encoding='utf-8-sig',newline='') as f:
                writer=csv.writer(f,delimiter=';');writer.writerow(['Размер','Единицы','Выборка','Левая граница','Правая граница','Количество','Доля (%)','Калибровка'])
                for left,right,count in zip(r['edges'][:-1],r['edges'][1:],r['counts']):
                    writer.writerow([self.metric.get(),self.unit.get(),self.population.get(),f'{left:.8g}'.replace('.',','),
                        f'{right:.8g}'.replace('.',','),int(count),f'{count/r["n"]*100:.6f}'.replace('.',','),self.app.calibration if self.unit.get()=='мкм' else ''])
        except Exception as e:messagebox.showerror('Экспорт',str(e),parent=self.window)
