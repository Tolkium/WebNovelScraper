# Standard library imports for GUI
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import json
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Dict, Any, Tuple

# Import your scraper classes
from LightNovelScraper import (
    ScraperConfig,
    UserConfig,
    NovelScraper,
    setup_logging
)

if TYPE_CHECKING:
    from LightNovelScraper import NovelScraper

class NovelScraperGUI:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("Light Novel Scraper")
        self.window.geometry("800x900")
        
        # Create main notebook for tabs
        self.notebook = ttk.Notebook(self.window)
        self.notebook.pack(expand=True, fill='both', padx=5, pady=5)
        
        # Create tabs
        self.main_tab = ttk.Frame(self.notebook)
        self.user_config_tab = ttk.Frame(self.notebook)
        self.scraper_config_tab = ttk.Frame(self.notebook)
        
        self.notebook.add(self.main_tab, text="Main")
        self.notebook.add(self.user_config_tab, text="User Config")
        self.notebook.add(self.scraper_config_tab, text="Scraper Config")
        
        # Initialize configuration
        self.load_config()
        
        # Setup UI elements
        self.setup_main_tab()
        self.setup_user_config_tab()
        self.setup_scraper_config_tab()
        
        # Add start button at the bottom
        self.start_button = ttk.Button(
            self.window,
            text="Start Scraping",
            command=self.start_scraping
        )
        self.start_button.pack(pady=10)
        
        # Add status label
        self.status_var = tk.StringVar()
        self.status_label = ttk.Label(
            self.window,
            textvariable=self.status_var,
            wraplength=780
        )
        self.status_label.pack(pady=5)

    def load_config(self):
        """Load configuration from file or use defaults."""
        self.config_file = Path("scraper_config.json")
        
        # Default configuration
        self.config = {
            "main": {
                "base_url": "",
                "novel_id": "",
                "start_chapter": "1",
                "end_chapter": "1",
                "novel_title": "",
                "cover_url": "",
                "author": "",
                "translator": "",
                "creator": "LightNovelScraper"
            },
            "user_config": {
                "include_footnotes": False,
                "include_chapter_notes": False,
                "simple_chapter_names": False,
                "debug_mode": False,
                "extraction_methods": [
                    "known_selectors",
                    "pattern_matching",
                    "density",
                    "custom"
                ],
                "custom_selectors": []
            },
            "scraper_config": {
                "timeout": 30,
                "max_retries": 3,
                "requests_per_second": 20.0,
                "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/91.0.4472.124 Safari/537.36"),
                "min_content_length": 100,
                "max_cover_height": 2400,
                "cover_aspect_ratio": 0.666667,
                "jpeg_quality": 95,
                "max_backoff_time": 300,
                "retry_delay_multiplier": 5,
                "rate_limiter_queue_size": 10,
                "note_markers": [
                    "t/n:", "tn:", "t/l:", "tl:",
                    "translator's note:", "translator note:",
                    "chapter note:", "chapter note by",
                    "thanks for playing", "as always"
                ],
                "ad_selectors": [
                    "script", ".CTGIiSmv", ".L-MHncuP", ".vm-placement",
                    "div[data-defid]", "div[data-mobid]"
                ],
                "css_style": """body { 
                    font-family: Times New Roman, serif; 
                    margin: 5%; 
                    text-align: justify; 
                }
                h1 { 
                    text-align: center; 
                    font-size: 2em;
                    margin: 1em 0 1em 0;
                }
                h2 { margin-top: 1em; }
                h3 { margin-top: 1em; }
                p { margin: 1em 0; }
                .chapter-content {
                    line-height: 1.6;
                }
                .footnotes {
                    margin-top: 3em;
                    font-size: 0.9em;
                    border-top: 1px solid #cccccc;
                    padding-top: 1em;
                }
                .footnote {
                    margin: 0.5em 0;
                    font-size: 0.9em;
                }
                .chapter-notes {
                    margin-top: 3em;
                    padding-top: 1em;
                    border-top: 1px solid #ccc;
                    font-size: 0.9em;
                }
                .note {
                    font-style: italic;
                    margin: 0.5em 0;
                }
                sup {
                    vertical-align: super;
                    font-size: 0.8em;
                }
                a {
                    color: #000000;
                    text-decoration: none;
                }"""
            }
        }
        
        # Load saved configuration if exists
        if self.config_file.exists():
            try:
                saved_config = json.loads(self.config_file.read_text())
                self.config.update(saved_config)
            except Exception as e:
                print(f"Error loading config: {e}")



    def setup_main_tab(self):
        """Setup the main tab with basic novel information inputs."""
        main_frame = ttk.LabelFrame(self.main_tab, text="Novel Information")
        main_frame.pack(expand=True, fill='both', padx=5, pady=5)

        # Create and layout input fields
        self.main_inputs = {}
        main_fields = [
            ("base_url", "Base URL:", ""),
            ("novel_id", "Novel ID:", ""),
            ("start_chapter", "Start Chapter:", "1"),
            ("end_chapter", "End Chapter:", "1"),
            ("novel_title", "Novel Title:", ""),
            ("cover_url", "Cover URL (optional):", ""),
            ("author", "Author (optional):", ""),
            ("translator", "Translator (optional):", ""),
            ("creator", "Creator:", "LightNovelScraper")
        ]

        for row, (field_id, label_text, default) in enumerate(main_fields):
            # Label
            ttk.Label(main_frame, text=label_text).grid(
                row=row, column=0, padx=5, pady=5, sticky='e'
            )
            
            # Entry field
            entry = ttk.Entry(main_frame, width=60)
            entry.grid(row=row, column=1, padx=5, pady=5, sticky='ew')
            entry.insert(0, self.config["main"].get(field_id, default))
            self.main_inputs[field_id] = entry

        # Configure grid column weights
        main_frame.columnconfigure(1, weight=1)

    def create_list_editor(
        self,
        parent: ttk.Frame,
        title: str,
        items: list,
        side: str = 'left'
    ) -> tuple[tk.Listbox, list]:
        """Create a list editor widget with add/remove buttons."""
        frame = ttk.LabelFrame(parent, text=title)
        frame.pack(side=side, fill='both', expand=True, padx=5, pady=5)
        
        # Create inner frame for horizontal layout
        inner_frame = ttk.Frame(frame)
        inner_frame.pack(fill='both', expand=True, padx=5, pady=5)
        
        # Left side: Listbox with scrollbar
        list_frame = ttk.Frame(inner_frame)
        list_frame.pack(side='left', fill='both', expand=True)
        
        # Create listbox with scrollbar
        listbox = tk.Listbox(list_frame, height=5)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        
        listbox.configure(yscrollcommand=scrollbar.set)
        
        # Pack listbox and scrollbar
        listbox.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # Right side: Buttons stacked vertically
        btn_frame = ttk.Frame(inner_frame)
        btn_frame.pack(side='right', padx=(5, 0), fill='y')
        
        # Add button
        def add_item():
            new_item = self.show_input_dialog(f"Add new {title}", "Enter value:")
            if new_item:
                listbox.insert(tk.END, new_item)

        ttk.Button(
            btn_frame,
            text="Add",
            command=add_item
        ).pack(side='top', pady=(0, 5))

        # Remove button
        def remove_item():
            selection = listbox.curselection()
            if selection:
                listbox.delete(selection[0])

        ttk.Button(
            btn_frame,
            text="Remove",
            command=remove_item
        ).pack(side='top')

        # Add initial items
        for item in items:
            listbox.insert(tk.END, item)

        # Unbind the mousewheel from the main canvas when mouse is over the listbox
        def on_enter(event):
            canvas = self._find_canvas(event.widget)
            if canvas:
                canvas.unbind_all("<MouseWheel>")
        
        def on_leave(event):
            canvas = self._find_canvas(event.widget)
            if canvas:
                canvas.bind_all("<MouseWheel>", 
                            lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        
        listbox.bind('<Enter>', on_enter)
        listbox.bind('<Leave>', on_leave)
        
        # Bind mousewheel to listbox scroll
        def on_mousewheel(event):
            listbox.yview_scroll(int(-1*(event.delta/120)), "units")
        
        listbox.bind('<MouseWheel>', on_mousewheel)

        return listbox, items

    def _find_canvas(self, widget):
        """Helper function to find the parent canvas of a widget."""
        parent = widget.winfo_parent()
        while parent:
            parent_widget = widget._nametowidget(parent)
            if isinstance(parent_widget, tk.Canvas):
                return parent_widget
            parent = parent_widget.winfo_parent()
            if not parent:
                break
        return None

    def create_text_editor(
        self,
        parent: ttk.Frame,
        title: str,
        text: str,
        height: int = 10
    ) -> scrolledtext.ScrolledText:
        """Create a scrolled text editor widget."""
        frame = ttk.LabelFrame(parent, text=title)
        frame.pack(fill='both', expand=True, padx=5, pady=5)

        text_widget = scrolledtext.ScrolledText(frame, height=height)
        text_widget.pack(fill='both', expand=True, padx=5, pady=5)
        text_widget.insert('1.0', text)

        return text_widget

    def show_input_dialog(self, title: str, prompt: str) -> str:
        """Show an input dialog and return the entered value."""
        dialog = tk.Toplevel(self.window)
        dialog.title(title)
        dialog.geometry("300x100")
        dialog.transient(self.window)
        dialog.grab_set()

        ttk.Label(dialog, text=prompt).pack(padx=5, pady=5)
        entry = ttk.Entry(dialog, width=40)
        entry.pack(padx=5, pady=5)
        
        result = None

        def on_ok():
            nonlocal result
            result = entry.get()
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill='x', padx=5, pady=5)
        ttk.Button(btn_frame, text="OK", command=on_ok).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side='left')

        dialog.wait_window()
        return result

    def show_message(self, title: str, message: str, message_type: str = "info"):
        """Show a message dialog."""
        if message_type == "error":
            tk.messagebox.showerror(title, message)
        elif message_type == "warning":
            tk.messagebox.showwarning(title, message)
        else:
            tk.messagebox.showinfo(title, message)

    def validate_inputs(self) -> bool:
        """Validate all inputs before starting scraping."""
        # Validate required fields
        required_fields = [
            ("base_url", "Base URL"),
            ("novel_id", "Novel ID"),
            ("novel_title", "Novel Title")
        ]

        for field_id, field_name in required_fields:
            if not self.main_inputs[field_id].get().strip():
                self.show_message(
                    "Validation Error",
                    f"{field_name} is required.",
                    "error"
                )
                return False

        # Validate chapter numbers
        try:
            start = int(self.main_inputs["start_chapter"].get())
            end = int(self.main_inputs["end_chapter"].get())
            if start < 1 or end < start:
                raise ValueError
        except ValueError:
            self.show_message(
                "Validation Error",
                "Invalid chapter numbers. Start chapter must be positive and not greater than end chapter.",
                "error"
            )
            return False

        return True



    def setup_user_config_tab(self):
        """Setup the user configuration tab."""
        # Create main frame
        frame = ttk.Frame(self.user_config_tab)
        frame.pack(expand=True, fill='both', padx=5, pady=5)

        # Checkboxes frame
        checkbox_frame = ttk.LabelFrame(frame, text="Options")
        checkbox_frame.pack(fill='x', padx=5, pady=5)

        # Checkboxes
        self.user_config_vars = {}
        checkboxes = [
            ("include_footnotes", "Include Footnotes"),
            ("include_chapter_notes", "Include Chapter Notes"),
            ("simple_chapter_names", "Use Simple Chapter Names"),
            ("debug_mode", "Debug Mode")
        ]

        for var_name, text in checkboxes:
            var = tk.BooleanVar(
                value=self.config["user_config"].get(var_name, False)
            )
            self.user_config_vars[var_name] = var
            ttk.Checkbutton(
                checkbox_frame,
                text=text,
                variable=var
            ).pack(anchor='w', padx=5, pady=2)

        # Extraction methods
        methods_frame = ttk.LabelFrame(frame, text="Extraction Methods")
        methods_frame.pack(fill='x', padx=5, pady=5)

        self.extraction_methods_vars = {}
        available_methods = [
            ("known_selectors", "Known Selectors"),
            ("pattern_matching", "Pattern Matching"),
            ("density", "Text Density Analysis"),
            ("custom", "Custom Selectors")
        ]

        current_methods = self.config["user_config"]["extraction_methods"]
        for method_id, method_name in available_methods:
            var = tk.BooleanVar(value=method_id in current_methods)
            self.extraction_methods_vars[method_id] = var
            ttk.Checkbutton(
                methods_frame,
                text=method_name,
                variable=var
            ).pack(anchor='w', padx=5, pady=2)

        # Custom selectors
        self.custom_selectors_list, _ = self.create_list_editor(
            frame,
            "Custom CSS Selectors",
            self.config["user_config"]["custom_selectors"]
        )

    def setup_scraper_config_tab(self):
        """Setup the scraper configuration tab."""
        # Configure the tab to expand
        self.scraper_config_tab.columnconfigure(0, weight=1)
        self.scraper_config_tab.rowconfigure(0, weight=1)

        # Create main frame inside tab - changed to direct frame without canvas
        main_frame = ttk.Frame(self.scraper_config_tab)
        main_frame.grid(row=0, column=0, sticky='nsew')
        main_frame.columnconfigure(0, weight=1)

        # Network settings frame
        network_frame = ttk.LabelFrame(main_frame, text="Network Settings")
        network_frame.pack(fill='x', padx=5, pady=5)
        network_frame.columnconfigure(1, weight=1)

        self.scraper_config_inputs = {}
        network_fields = [
            ("timeout", "Timeout (seconds):", 30),
            ("max_retries", "Max Retries:", 3),
            ("requests_per_second", "Requests per Second:", 20.0),
            ("user_agent", "User Agent:", "Mozilla/5.0 ..."),
        ]

        for row, (field_id, label_text, default) in enumerate(network_fields):
            ttk.Label(network_frame, text=label_text).grid(
                row=row, column=0, padx=5, pady=5, sticky='e'
            )
            
            entry = ttk.Entry(network_frame)
            entry.grid(row=row, column=1, padx=5, pady=5, sticky='ew')
            entry.insert(0, self.config["scraper_config"].get(field_id, default))
            self.scraper_config_inputs[field_id] = entry

        # Content settings frame
        content_frame = ttk.LabelFrame(main_frame, text="Content Settings")
        content_frame.pack(fill='x', padx=5, pady=5)
        content_frame.columnconfigure(1, weight=1)

        content_fields = [
            ("min_content_length", "Min Content Length:", 100),
            ("max_cover_height", "Max Cover Height:", 2400),
            ("cover_aspect_ratio", "Cover Aspect Ratio:", 0.666667),
            ("jpeg_quality", "JPEG Quality:", 95),
        ]

        for row, (field_id, label_text, default) in enumerate(content_fields):
            ttk.Label(content_frame, text=label_text).grid(
                row=row, column=0, padx=5, pady=5, sticky='e'
            )
            
            entry = ttk.Entry(content_frame)
            entry.grid(row=row, column=1, padx=5, pady=5, sticky='ew')
            entry.insert(0, self.config["scraper_config"].get(field_id, default))
            self.scraper_config_inputs[field_id] = entry

        # Rate limiting frame
        rate_frame = ttk.LabelFrame(main_frame, text="Rate Limiting")
        rate_frame.pack(fill='x', padx=5, pady=5)
        rate_frame.columnconfigure(1, weight=1)

        rate_fields = [
            ("max_backoff_time", "Max Backoff Time:", 300),
            ("retry_delay_multiplier", "Retry Delay Multiplier:", 5),
            ("rate_limiter_queue_size", "Rate Limiter Queue Size:", 10),
        ]

        for row, (field_id, label_text, default) in enumerate(rate_fields):
            ttk.Label(rate_frame, text=label_text).grid(
                row=row, column=0, padx=5, pady=5, sticky='e'
            )
            
            entry = ttk.Entry(rate_frame)
            entry.grid(row=row, column=1, padx=5, pady=5, sticky='ew')
            entry.insert(0, self.config["scraper_config"].get(field_id, default))
            self.scraper_config_inputs[field_id] = entry

        # Lists Container Frame
        lists_frame = ttk.Frame(main_frame)
        lists_frame.pack(fill='x', padx=5, pady=5)
        lists_frame.columnconfigure(0, weight=1)
        lists_frame.columnconfigure(1, weight=1)

        # Note Markers (left side)
        self.note_markers_list, _ = self.create_list_editor(
            lists_frame,
            "Note Markers",
            self.config["scraper_config"]["note_markers"],
            side='left'
        )

        # Ad Selectors (right side)
        self.ad_selectors_list, _ = self.create_list_editor(
            lists_frame,
            "Ad Selectors",
            self.config["scraper_config"]["ad_selectors"],
            side='left'
        )

        # CSS Style
        self.css_style_text = self.create_text_editor(
            main_frame,
            "CSS Style",
            self.config["scraper_config"]["css_style"]
        )

    def save_config(self) -> None:
        """Save current configuration to file."""
        try:
            # Gather main settings
            self.config["main"] = {
                field: widget.get()
                for field, widget in self.main_inputs.items()
            }

            # Gather user config
            self.config["user_config"] = {
                field: var.get()
                for field, var in self.user_config_vars.items()
            }
            
            # Get selected extraction methods
            self.config["user_config"]["extraction_methods"] = [
                method for method, var in self.extraction_methods_vars.items()
                if var.get()
            ]

            # Get custom selectors
            self.config["user_config"]["custom_selectors"] = list(
                self.custom_selectors_list.get(0, tk.END)
            )

            # Gather scraper config
            self.config["scraper_config"] = {
                field: widget.get()
                for field, widget in self.scraper_config_inputs.items()
            }

            # Get lists
            self.config["scraper_config"]["note_markers"] = list(
                self.note_markers_list.get(0, tk.END)
            )
            self.config["scraper_config"]["ad_selectors"] = list(
                self.ad_selectors_list.get(0, tk.END)
            )

            # Get CSS style
            self.config["scraper_config"]["css_style"] = (
                self.css_style_text.get("1.0", tk.END).strip()
            )

            # Save to file
            self.config_file.write_text(
                json.dumps(self.config, indent=4)
            )
            
        except Exception as e:
            self.show_message(
                "Error",
                f"Failed to save configuration: {str(e)}",
                "error"
            )





    def start_scraping(self):
        """Start the scraping process."""
        if not self.validate_inputs():
            return

        # Save current configuration
        self.save_config()

        try:
            # Update status
            self.status_var.set("Initializing scraper...")
            self.window.update()

            # Create configurations
            scraper_config = ScraperConfig(
                timeout=int(self.scraper_config_inputs["timeout"].get()),
                max_retries=int(self.scraper_config_inputs["max_retries"].get()),
                requests_per_second=float(self.scraper_config_inputs["requests_per_second"].get()),
                user_agent=self.scraper_config_inputs["user_agent"].get(),
                min_content_length=int(self.scraper_config_inputs["min_content_length"].get()),
                max_cover_height=int(self.scraper_config_inputs["max_cover_height"].get()),
                cover_aspect_ratio=float(self.scraper_config_inputs["cover_aspect_ratio"].get()),
                jpeg_quality=int(self.scraper_config_inputs["jpeg_quality"].get()),
                max_backoff_time=int(self.scraper_config_inputs["max_backoff_time"].get()),
                retry_delay_multiplier=int(self.scraper_config_inputs["retry_delay_multiplier"].get()),
                rate_limiter_queue_size=int(self.scraper_config_inputs["rate_limiter_queue_size"].get()),
                note_markers=list(self.note_markers_list.get(0, tk.END)),
                ad_selectors=list(self.ad_selectors_list.get(0, tk.END)),
                css_style=self.css_style_text.get("1.0", tk.END).strip()
            )

            user_config = UserConfig(
                include_footnotes=self.user_config_vars["include_footnotes"].get(),
                include_chapter_notes=self.user_config_vars["include_chapter_notes"].get(),
                simple_chapter_names=self.user_config_vars["simple_chapter_names"].get(),
                debug_mode=self.user_config_vars["debug_mode"].get(),
                extraction_methods=[
                    method for method, var in self.extraction_methods_vars.items()
                    if var.get()
                ]
            )

            # Setup logger
            logger = setup_logging(user_config.debug_mode)

            # Initialize scraper - removed custom_selectors argument
            scraper = NovelScraper(
                scraper_config,
                user_config,
                logger
            )

            # Set custom selectors to content extractor if needed
            if hasattr(scraper, 'content_extractor'):
                scraper.content_extractor.custom_selectors = list(
                    self.custom_selectors_list.get(0, tk.END)
                )

            # Get metadata
            metadata = {
                'author': self.main_inputs["author"].get() or 'Unknown',
                'translator': self.main_inputs["translator"].get(),
                'creator': self.main_inputs["creator"].get(),
                'cover_url': self.main_inputs["cover_url"].get()
            }

            # Start scraping in a separate thread
            self.start_button.configure(state='disabled')
            threading.Thread(
                target=self._run_scraping,
                args=(scraper, metadata),
                daemon=True
            ).start()

        except Exception as e:
            self.status_var.set(f"Error initializing scraper: {str(e)}")
            self.start_button.configure(state='normal')

    def _run_scraping(self, scraper: 'NovelScraper', metadata: Dict[str, str]):
        """Run the scraping process in a separate thread."""
        try:
            success = scraper.scrape_novel(
                self.main_inputs["base_url"].get(),
                self.main_inputs["novel_id"].get(),
                int(self.main_inputs["start_chapter"].get()),
                int(self.main_inputs["end_chapter"].get()),
                self.main_inputs["novel_title"].get(),
                metadata
            )

            if success:
                self.status_var.set("Scraping completed successfully!")
            else:
                self.status_var.set("Scraping failed. Check the logs for details.")

        except Exception as e:
            self.status_var.set(f"Error during scraping: {str(e)}")

        finally:
            self.start_button.configure(state='normal')
            self.window.update()

    def run(self):
        """Start the GUI application."""
        self.window.mainloop()




def main():
    """Main entry point for the GUI application."""
    # Required imports for the GUI
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox
    import threading
    import json
    from pathlib import Path
    
    # Import your existing scraper classes
    from LightNovelScraper import (
        ScraperConfig, UserConfig, NovelScraper,
        setup_logging
    )
    
    try:
        app = NovelScraperGUI()
        app.run()
    except Exception as e:
        tk.messagebox.showerror(
            "Error",
            f"Application error: {str(e)}\n\nPlease check the logs for details."
        )

if __name__ == "__main__":
    main()