import tkinter as tk
from tkinter import messagebox
import json
import os
import time

class LayoutManagerGUI(tk.Toplevel):
    """Ventana de gestión de layouts con interfaz gráfica completa"""
    
    def __init__(self, parent, layout_manager):
        super().__init__(parent)
        self.parent = parent
        self.layout_manager = layout_manager
        self.title("Gestor de Layouts")
        self.geometry("600x500")
        self.resizable(True, True)
        self.configure(bg=parent.BG)
        
        # Centrar ventana
        self.transient(parent)
        self.grab_set()
        
        self._setup_ui()
        self._refresh_layouts()
    
    def _setup_ui(self):
        # Header
        header = tk.Frame(self, bg=self.parent.PANEL, height=60)
        header.pack(fill="x")
        header.pack_propagate(False)
        
        tk.Label(header, text="📋 Gestor de Layouts", bg=self.parent.PANEL, fg=self.parent.TEXT,
                 font=self.parent.font_title).pack(side="left", padx=20, pady=15)
        
        # Botón cerrar
        close_btn = tk.Button(header, text="✕", bg=self.parent.RED, fg="white",
                            font=("Segoe UI", 12, "bold"), relief="flat", cursor="hand2",
                            command=self.destroy)
        close_btn.pack(side="right", padx=20, pady=15)
        
        # Frame principal con scroll
        main_frame = tk.Frame(self, bg=self.parent.BG)
        main_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        # Sección de acciones rápidas
        actions_frame = tk.Frame(main_frame, bg=self.parent.CARD, highlightthickness=1,
                               highlightbackground=self.parent.BORDER)
        actions_frame.pack(fill="x", pady=(0, 15))
        
        actions_inner = tk.Frame(actions_frame, bg=self.parent.CARD)
        actions_inner.pack(fill="x", padx=15, pady=15)
        
        tk.Label(actions_inner, text="Acciones Rápidas", bg=self.parent.CARD, fg=self.parent.TEXT,
                 font=self.parent.font_label).pack(anchor="w", pady=(0, 10))
        
        buttons_frame = tk.Frame(actions_inner, bg=self.parent.CARD)
        buttons_frame.pack(fill="x")
        
        # Botón guardar layout actual
        save_btn = tk.Button(buttons_frame, text="💾 Guardar Layout Actual",
                           bg=self.parent.GREEN, fg="white", font=self.parent.font_label,
                           relief="flat", cursor="hand2", command=self._save_current_layout)
        save_btn.pack(side="left", padx=(0, 10))
        
        # Botón importar desde Wintabber
        import_btn = tk.Button(buttons_frame, text="📥 Importar de Wintabber",
                             bg=self.parent.TEAL, fg="white", font=self.parent.font_label,
                             relief="flat", cursor="hand2", command=self._import_from_wintabber)
        import_btn.pack(side="left", padx=(0, 10))
        
        # Botón refresh
        refresh_btn = tk.Button(buttons_frame, text="🔄 Actualizar",
                              bg=self.parent.ACCENT, fg="white", font=self.parent.font_label,
                              relief="flat", cursor="hand2", command=self._refresh_layouts)
        refresh_btn.pack(side="left")
        
        # Lista de layouts
        list_frame = tk.Frame(main_frame, bg=self.parent.CARD, highlightthickness=1,
                             highlightbackground=self.parent.BORDER)
        list_frame.pack(fill="both", expand=True)
        
        list_inner = tk.Frame(list_frame, bg=self.parent.CARD)
        list_inner.pack(fill="both", expand=True, padx=15, pady=15)
        
        tk.Label(list_inner, text="Layouts Disponibles", bg=self.parent.CARD, fg=self.parent.TEXT,
                 font=self.parent.font_label).pack(anchor="w", pady=(0, 10))
        
        # Scrollable listbox
        list_container = tk.Frame(list_inner, bg=self.parent.CARD)
        list_container.pack(fill="both", expand=True)
        
        scrollbar = tk.Scrollbar(list_container)
        scrollbar.pack(side="right", fill="y")
        
        self.layouts_listbox = tk.Listbox(list_container, bg=self.parent.PANEL, fg=self.parent.TEXT,
                                         font=("Segoe UI", 9), relief="flat", highlightthickness=0,
                                         yscrollcommand=scrollbar.set)
        self.layouts_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.layouts_listbox.yview)
        
        # Bindings
        self.layouts_listbox.bind("<<ListboxSelect>>", self._on_layout_select)
        self.layouts_listbox.bind("<Double-Button-1>", self._apply_selected_layout)
        
        # Frame de detalles y acciones
        details_frame = tk.Frame(list_inner, bg=self.parent.CARD)
        details_frame.pack(fill="x", pady=(10, 0))
        
        self.details_label = tk.Label(details_frame, text="Selecciona un layout para ver detalles",
                                    bg=self.parent.CARD, fg=self.parent.TEXT_DIM,
                                    font=self.parent.font_sub, wraplength=400)
        self.details_label.pack(anchor="w", pady=(0, 10))
        
        # Botones de acción para layout seleccionado
        self.action_buttons_frame = tk.Frame(details_frame, bg=self.parent.CARD)
        self.action_buttons_frame.pack(fill="x")
        
        self.apply_btn = tk.Button(self.action_buttons_frame, text="✅ Aplicar Layout",
                                 bg=self.parent.GREEN, fg="white", font=self.parent.font_label,
                                 relief="flat", cursor="hand2", state="disabled",
                                 command=self._apply_selected_layout)
        self.apply_btn.pack(side="left", padx=(0, 10))
        
        self.delete_btn = tk.Button(self.action_buttons_frame, text="🗑️ Eliminar",
                                  bg=self.parent.RED, fg="white", font=self.parent.font_label,
                                  relief="flat", cursor="hand2", state="disabled",
                                  command=self._delete_selected_layout)
        self.delete_btn.pack(side="left")
    
    def _refresh_layouts(self):
        """Actualizar la lista de layouts"""
        self.layouts_listbox.delete(0, tk.END)
        
        layouts = self.layout_manager.get_available_layouts()
        
        if not layouts:
            self.layouts_listbox.insert(tk.END, "No hay layouts guardados")
            self.details_label.config(text="No hay layouts disponibles. Guarda uno o importa desde Wintabber.")
            return
        
        self.layout_data = layouts
        for name in layouts.keys():
            self.layouts_listbox.insert(tk.END, name)
    
    def _on_layout_select(self, event):
        """Cuando se selecciona un layout"""
        selection = self.layouts_listbox.curselection()
        
        if not selection or not hasattr(self, 'layout_data'):
            return
        
        index = selection[0]
        layout_names = list(self.layout_data.keys())
        
        if index >= len(layout_names):
            return
        
        layout_name = layout_names[index]
        layout_info = self.layout_data[layout_name]
        
        # Mostrar detalles
        details = f"📝 {layout_info.get('Description', 'Sin descripción')}\n"
        details += f"🔢 {len(layout_info.get('Positions', []))} posiciones\n"
        details += f"📅 Creado: {layout_info.get('CreatedAt', 'Desconocido')}"
        
        self.details_label.config(text=details)
        
        # Habilitar botones
        self.apply_btn.config(state="normal")
        self.delete_btn.config(state="normal")
    
    def _apply_selected_layout(self, event=None):
        """Aplicar el layout seleccionado"""
        selection = self.layouts_listbox.curselection()
        
        if not selection or not hasattr(self, 'layout_data'):
            return
        
        index = selection[0]
        layout_names = list(self.layout_data.keys())
        
        if index >= len(layout_names):
            return
        
        layout_name = layout_names[index]
        
        # Aplicar layout
        if self.layout_manager.apply_layout_to_slots(layout_name):
            messagebox.showinfo("Éxito", f"Layout '{layout_name}' aplicado correctamente")
        else:
            messagebox.showerror("Error", f"No se pudo aplicar el layout '{layout_name}'")
    
    def _delete_selected_layout(self):
        """Eliminar el layout seleccionado"""
        selection = self.layouts_listbox.curselection()
        
        if not selection or not hasattr(self, 'layout_data'):
            return
        
        index = selection[0]
        layout_names = list(self.layout_data.keys())
        
        if index >= len(layout_names):
            return
        
        layout_name = layout_names[index]
        
        # Confirmar eliminación
        result = messagebox.askyesno("Confirmar Eliminación", 
                                     f"¿Estás seguro que deseas eliminar el layout '{layout_name}'?")
        
        if result:
            # Eliminar del archivo
            layouts = self.layout_manager.get_available_layouts()
            if layout_name in layouts:
                del layouts[layout_name]
                
                try:
                    os.makedirs(os.path.dirname(self.layout_manager.config_path), exist_ok=True)
                    with open(self.layout_manager.config_path, 'w', encoding='utf-8') as f:
                        json.dump(layouts, f, indent=2, ensure_ascii=False)
                    
                    messagebox.showinfo("Éxito", f"Layout '{layout_name}' eliminado correctamente")
                    self._refresh_layouts()
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo eliminar el layout: {e}")
    
    def _save_current_layout(self):
        """Guardar el layout actual"""
        global orden_personajes
        
        if not orden_personajes:
            messagebox.showwarning("Advertencia", "No hay personajes detectados para guardar")
            return
        
        # Dialogo para nombre y descripción
        dialog = tk.Toplevel(self)
        dialog.title("Guardar Layout")
        dialog.geometry("400x200")
        dialog.resizable(False, False)
        dialog.configure(bg=self.parent.BG)
        dialog.transient(self)
        dialog.grab_set()
        
        # Centrar
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (400 // 2)
        y = (dialog.winfo_screenheight() // 2) - (200 // 2)
        dialog.geometry(f"400x200+{x}+{y}")
        
        # Contenido
        main_frame = tk.Frame(dialog, bg=self.parent.BG)
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        tk.Label(main_frame, text="Nombre del Layout:", bg=self.parent.BG, fg=self.parent.TEXT,
                 font=self.parent.font_label).pack(anchor="w", pady=(0, 5))
        
        name_entry = tk.Entry(main_frame, bg=self.parent.PANEL, fg=self.parent.TEXT,
                             font=("Segoe UI", 10), relief="flat", highlightthickness=1,
                             highlightbackground=self.parent.BORDER)
        name_entry.pack(fill="x", pady=(0, 15))
        name_entry.focus()
        
        tk.Label(main_frame, text="Descripción (opcional):", bg=self.parent.BG, fg=self.parent.TEXT,
                 font=self.parent.font_label).pack(anchor="w", pady=(0, 5))
        
        desc_entry = tk.Entry(main_frame, bg=self.parent.PANEL, fg=self.parent.TEXT,
                             font=("Segoe UI", 10), relief="flat", highlightthickness=1,
                             highlightbackground=self.parent.BORDER)
        desc_entry.pack(fill="x", pady=(0, 20))
        
        # Botones
        btn_frame = tk.Frame(main_frame, bg=self.parent.BG)
        btn_frame.pack(fill="x")
        
        def save():
            name = name_entry.get().strip()
            if not name:
                messagebox.showwarning("Advertencia", "Debes ingresar un nombre para el layout")
                return
            
            description = desc_entry.get().strip()
            
            if self.layout_manager.save_current_layout(name, description):
                messagebox.showinfo("Éxito", f"Layout '{name}' guardado correctamente")
                self._refresh_layouts()
                dialog.destroy()
            else:
                messagebox.showerror("Error", "No se pudo guardar el layout")
        
        tk.Button(btn_frame, text="Guardar", bg=self.parent.GREEN, fg="white",
                 font=self.parent.font_label, relief="flat", cursor="hand2",
                 command=save).pack(side="right")
        
        tk.Button(btn_frame, text="Cancelar", bg=self.parent.RED_DIM, fg="white",
                 font=self.parent.font_label, relief="flat", cursor="hand2",
                 command=dialog.destroy).pack(side="right", padx=(0, 10))
    
    def _import_from_wintabber(self):
        """Importar layouts desde Wintabber (usando consola por ahora)"""
        messagebox.showinfo("Importar", "Usando consola para importar layouts de Wintabber Dofus...")
        self.layout_manager.show_layout_menu()
        self._refresh_layouts()
