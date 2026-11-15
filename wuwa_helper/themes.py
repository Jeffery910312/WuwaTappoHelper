"""Shared light and dark colors for the desktop interface."""
from tkinter import TclError

UI_FONT = ('Microsoft JhengHei UI', 11)

PALETTES = {
    'light': dict(page='#f5f5f0', card='#fffefa', text='#233c36', muted='#687a73',
                  line='#dce4de', accent='#147d70', hover='#106b60',
                  soft='#e5f2ec', quiet='#eaf0eb', alert='#946021', alert_bg='#fff0d9',
                  on_accent='#ffffff', disabled='#98a59e'),
    'dark': dict(page='#111b20', card='#1a272d', text='#edf5f0', muted='#a6bbb1',
                 line='#34474b', accent='#6bdbc0', hover='#8be8d0',
                 soft='#24483f', quiet='#26363c', alert='#f2bf76', alert_bg='#433323',
                 on_accent='#103a2f', disabled='#71867e'),
}


def configure_theme(root, style, name):
    """Recolor existing ttk styles and future popup lists without rebuilding UI."""
    palette = PALETTES[name]
    root.configure(bg=palette['page'])
    for option, color in (('background', palette['card']), ('foreground', palette['text']),
                          ('selectBackground', palette['accent']),
                          ('selectForeground', palette['on_accent'])):
        root.option_add(f'*TCombobox*Listbox.{option}', color)
    root.option_add('*TCombobox*Listbox.font', UI_FONT)
    root.option_add('*TCombobox*Listbox.borderWidth', 0)
    root.option_add('*TCombobox*Listbox.highlightThickness', 0)
    style.configure('App.TFrame', background=palette['page'])
    style.configure('Card.TFrame', background=palette['card'])
    style.configure('App.TLabel', background=palette['page'], foreground=palette['text'], font=UI_FONT)
    style.configure('Card.TLabel', background=palette['card'], foreground=palette['text'], font=UI_FONT)
    style.configure('Muted.Card.TLabel', foreground=palette['muted'])
    style.configure('Title.App.TLabel', font=('Microsoft JhengHei UI', 19, 'bold'))
    style.configure('Heading.Card.TLabel', font=('Microsoft JhengHei UI', 11, 'bold'))
    style.configure('Count.Card.TLabel', font=('Segoe UI', 32, 'bold'))
    style.configure('Detail.App.TLabel', foreground=palette['muted'], font=('Microsoft JhengHei UI', 10))
    style.configure('Alert.Detail.App.TLabel', foreground=palette['alert'])
    style.configure('Tooltip.TLabel', background=palette['quiet'], foreground=palette['text'],
                    font=('Microsoft JhengHei UI', 10), padding=(10, 7))
    for label, background, foreground in (
            ('Idle', palette['quiet'], palette['muted']),
            ('Running', palette['soft'], palette['accent']),
            ('Alert', palette['alert_bg'], palette['alert'])):
        style.configure(f'{label}.Badge.TLabel', background=background, foreground=foreground,
                        font=('Microsoft JhengHei UI', 10, 'bold'), padding=(12, 6))
    for label, background in (('App', palette['page']), ('Card', palette['card'])):
        style.configure(f'{label}.TCheckbutton', background=background, foreground=palette['text'],
                        font=UI_FONT, padding=(0, 3), indicatorbackground=palette['quiet'],
                        indicatorforeground=palette['on_accent'])
        style.map(f'{label}.TCheckbutton', background=[('active', background)],
                  foreground=[('disabled', palette['disabled'])],
                  indicatorbackground=[('selected', palette['accent']), ('active', palette['line'])])
    style.configure('Card.TCombobox', foreground=palette['text'], fieldbackground=palette['card'],
                    background=palette['quiet'], bordercolor=palette['line'],
                    lightcolor=palette['line'], darkcolor=palette['line'],
                    arrowcolor=palette['muted'], padding=(9, 5))
    style.map('Card.TCombobox', fieldbackground=[('readonly', palette['card'])],
              selectbackground=[('readonly', palette['card'])],
              selectforeground=[('readonly', palette['text'])],
              foreground=[('disabled', palette['disabled'])],
              arrowcolor=[('active', palette['accent'])])
    style.configure('Mode.Toolbutton', font=UI_FONT, padding=(15, 7),
                    foreground=palette['muted'], background=palette['quiet'],
                    bordercolor=palette['line'], borderwidth=1, relief='flat')
    style.map('Mode.Toolbutton', background=[('selected', palette['accent']), ('active', palette['soft'])],
              foreground=[('selected', palette['on_accent']), ('active', palette['accent'])])
    style.configure('Count.Horizontal.TProgressbar', background=palette['accent'],
                    troughcolor=palette['soft'], borderwidth=0, thickness=8,
                    lightcolor=palette['accent'], darkcolor=palette['accent'])
    style.configure('Primary.TButton', font=UI_FONT, padding=(18, 11), borderwidth=0,
                    foreground=palette['on_accent'], background=palette['accent'])
    style.map('Primary.TButton', background=[('pressed', palette['hover']), ('active', palette['hover'])],
              foreground=[('disabled', palette['disabled'])])
    style.configure('Info.TButton', font=('Segoe UI Symbol', 12), padding=(3, 0),
                    foreground=palette['muted'], background=palette['card'],
                    bordercolor=palette['card'], borderwidth=0, relief='flat')
    style.map('Info.TButton', background=[('active', palette['soft'])],
              foreground=[('active', palette['accent'])])
    style.configure('Theme.TButton', font=('Segoe UI Symbol', 16), padding=(5, 0),
                    foreground=palette['text'], background=palette['page'],
                    bordercolor=palette['page'], borderwidth=0, relief='flat')
    style.map('Theme.TButton', background=[('active', palette['quiet'])],
              foreground=[('active', palette['accent'])])
    style.configure('Language.TButton', font=('Microsoft JhengHei UI', 10, 'bold'), padding=(5, 3),
                    foreground=palette['muted'], background=palette['page'],
                    bordercolor=palette['page'], borderwidth=0, relief='flat')
    style.map('Language.TButton', background=[('active', palette['quiet'])],
              foreground=[('active', palette['accent'])])
    return palette


def style_combobox_popup(combo, palette):
    """Keep an already-created Tk popup consistent after switching themes."""
    try:
        popup = combo.tk.call('ttk::combobox::PopdownWindow', str(combo))
        combo.tk.call(f'{popup}.f.l', 'configure', '-background', palette['card'],
                      '-foreground', palette['text'], '-selectbackground', palette['accent'],
                      '-selectforeground', palette['on_accent'], '-borderwidth', 0,
                      '-highlightthickness', 0)
    except TclError:
        # Tk implementations may use a different internal popup hierarchy. The
        # option database still supplies matching colors to new popup lists.
        pass
