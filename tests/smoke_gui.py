import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import converter

widgets = []
class Variable:
    def __init__(self, value=None): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value

class Widget:
    def __init__(self, parent=None, **kwargs):
        self.parent, self.options, self.bindings = parent, kwargs.copy(), {}
        self.command = kwargs.get('command')
        widgets.append(self)
    def grid(self, **kwargs): pass
    def pack(self, **kwargs): pass
    def configure(self, **kwargs): self.options.update(kwargs)
    config = configure
    def columnconfigure(self, *args, **kwargs): pass
    def rowconfigure(self, *args, **kwargs): pass
    def bind(self, event, callback): self.bindings[event] = callback
    def set(self, *args): pass
    def yview(self, *args): pass
    def xview(self, *args): pass
    def insert(self, *args, **kwargs): pass
    def see(self, *args): pass
    def invoke(self):
        if self.command: return self.command()

class Checkbutton(Widget):
    def invoke(self):
        variable = self.options['variable']
        variable.set(not variable.get())
        return super().invoke()

class Treeview(Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = {}
        self.selected = ()
    def heading(self, *args, **kwargs): pass
    def column(self, *args, **kwargs): pass
    def insert(self, parent, index, values):
        item = str(len(self.rows) + 1)
        self.rows[item] = dict(zip(('path', 'output', 'status'), values))
        return item
    def selection(self): return self.selected
    def set(self, item, key, value=None):
        if value is not None: self.rows[item][key] = value
        return self.rows[item][key]
    def get_children(self): return tuple(self.rows)
    def delete(self, *items):
        for item in items: self.rows.pop(item, None)

class Style:
    def __init__(self, root): self.configs = {}
    def theme_use(self, value): self.theme = value
    def configure(self, name, **kwargs): self.configs[name] = kwargs
    def map(self, *args, **kwargs): pass

class Root(Widget):
    def title(self, *args): pass
    def geometry(self, *args): pass
    def minsize(self, *args): pass
    def after(self, *args):
        if not hasattr(self, "scheduled"): self.scheduled = []
        self.scheduled.append(args)
    def protocol(self, *args): pass
    def option_add(self, *args): pass
    def mainloop(self): driver(self)
    def destroy(self): pass

class ImmediateThread:
    def __init__(self, target, args, daemon): self.target, self.args = target, args
    def start(self): self.target(*self.args)
    def is_alive(self): return False

captured = {}
class FakeConverter:
    def __init__(self, **settings): captured['settings'] = settings
    def run(self, paths, outputs):
        captured['paths'], captured['outputs'] = paths, outputs

temporary = tempfile.TemporaryDirectory()
base = Path(temporary.name)
pdfs = (str(base / 'example' / 'a.pdf'), str(base / 'other' / 'b.pdf'))
chosen = str(base / 'chosen-output')
opened = []

def find(text):
    return next(widget for widget in widgets if widget.options.get('text') == text)

def driver(root):
    assert root.options['background'] == '#171c25', 'Dark mode must be default'
    theme = find('Dark theme')
    theme.invoke()
    assert root.options['background'] == '#f3f5f8', 'Light mode must be usable'
    theme.invoke()
    assert root.options['background'] == '#171c25', 'Dark mode must toggle back'
    api = next(w for w in widgets if w.options.get('show') == '•')
    api.options['textvariable'].set('abc;def')
    show = find('Show tokens')
    show.invoke()
    assert api.options['show'] == ''
    show.invoke()
    assert api.options['show'] == '•'
    find('Add PDFs').invoke()
    tree = next(w for w in widgets if isinstance(w, Treeview))
    assert len(tree.rows) == 2
    tree.selected = tuple(tree.rows)
    find('Set selected output').invoke()
    assert all(row['output'] == chosen for row in tree.rows.values())
    find('Use source folder').invoke()
    assert [row['output'] for row in tree.rows.values()] == [str(Path(pdf).parent) for pdf in pdfs]
    tree.selected = (next(iter(tree.rows)),)
    find('Use default').invoke()
    assert tree.rows[tree.selected[0]]['output'] == 'Default'
    find('Start / Resume').invoke()
    assert captured['paths'] == list(pdfs)
    assert captured['outputs'] == {pdfs[1]: str(Path(pdfs[1]).parent)}
    assert captured['settings']['token'] == 'abc;def'
    connectivity = next(w for w in widgets if w.options.get('style') == 'Connectivity.TLabel')
    first_probe = next(args[1] for args in root.scheduled if args[0] == 0)
    first_probe()
    next(args[1] for args in root.scheduled if args[0] == 100)()
    assert connectivity.options['textvariable'].get() == 'Internet: available'
    last_probe = [args[1] for args in root.scheduled if args[0] == 10000][-1]
    last_probe()
    next(args[1] for args in root.scheduled if args[0] == 100)()
    assert 'unavailable' in connectivity.options['textvariable'].get()
    for label in ('MinerU', 'mojsadafi.ir', 'GitHub'):
        find(label).bindings['<Button-1>'](None)
    assert opened == ['https://mineru.net', 'https://mojsadafi.ir', 'https://github.com/mojjss']

tk = types.ModuleType('tkinter')
tk.Tk = Root
tk.StringVar = Variable
tk.BooleanVar = Variable
tk.filedialog = types.SimpleNamespace(askopenfilenames=lambda **kw: pdfs,
                                      askdirectory=lambda **kw: chosen,
                                      askopenfilename=lambda **kw: '')
tk.messagebox = types.SimpleNamespace(showinfo=lambda *args: None, showerror=lambda *args: None,
                                      showwarning=lambda *args: None)
ttk = types.ModuleType('tkinter.ttk')
for name in ('Frame', 'Label', 'Entry', 'Button', 'Combobox', 'Scrollbar'):
    setattr(ttk, name, Widget)
ttk.Checkbutton, ttk.Treeview, ttk.Style = Checkbutton, Treeview, Style
tk.ttk = ttk
scrolled = types.ModuleType('tkinter.scrolledtext')
scrolled.ScrolledText = Widget
with patch.dict(sys.modules, {'tkinter': tk, 'tkinter.ttk': ttk, 'tkinter.scrolledtext': scrolled}), \
     patch.object(converter, 'Converter', FakeConverter), \
     patch.object(converter.threading, 'Thread', ImmediateThread), \
     patch.object(converter, 'load_tokens', return_value=''), \
     patch.object(converter, 'save_tokens'), \
     patch.object(converter.webbrowser, 'open', side_effect=opened.append), \
     patch.object(converter, 'internet_reachable', side_effect=[True, False]):
    converter.main()
print('GUI callback simulation passed: mappings, source folder, connectivity, token visibility, theme, and links.')
