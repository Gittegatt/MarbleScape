"""Explicit decisions for narrowly repairable imports; closing means cancel."""


def confirm_import_repair(parent, label, changes, allow_skip=True):
    import tkinter as tk
    from tkinter import ttk

    result = ["no"]
    window = tk.Toplevel(parent)
    window.title("Incomplete profile")
    window.transient(parent)
    window.resizable(False, False)
    frame = ttk.Frame(window, padding=16)
    frame.grid(sticky="nsew")
    message = (f"{label}\n\nThe profile is missing fields with known safe defaults. "
               "All essential checks passed. Import it with these additions?\n\n"
               + "\n".join(changes)
               + "\n\nYes to all accepts these safe additions for this import only. "
               "Cancel aborts the entire import without changes.")
    message += (" Skip omits this profile." if allow_skip else
                " A full backup must be restored together; profiles cannot be skipped.")
    ttk.Label(frame, text=message, wraplength=600, justify="left").grid(
        row=0, column=0, columnspan=4, sticky="w", pady=(0, 16))

    def choose(value):
        result[0] = value
        window.destroy()

    choices = [("Yes", "yes"), ("Yes to all", "yes_all"), ("Cancel", "no")]
    if allow_skip:
        choices.extend((("Skip", "skip"), ("Skip all", "skip_all")))
    for column, (text, value) in enumerate(choices):
        button = ttk.Button(frame, text=text, width=14, command=lambda value=value: choose(value))
        button.grid(row=1 + column // 3, column=column % 3, padx=4, pady=4, sticky="e")
        if value == "no":
            button.focus_set()
    window.protocol("WM_DELETE_WINDOW", lambda: choose("no"))
    window.bind("<Escape>", lambda _event: choose("no"))
    previous_grab = parent.grab_current()
    window.grab_set()
    try:
        parent.wait_window(window)
    finally:
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
    return result[0]


def review_import_preflight(parent, report):
    """Scrollable complete report before any repair/conflict decisions or mutations."""
    import tkinter as tk
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText
    result = [False]
    window = tk.Toplevel(parent)
    window.title("Profile import - integrity check")
    window.transient(parent)
    frame = ttk.Frame(window, padding=12)
    frame.pack(fill="both", expand=True)
    text = ScrolledText(frame, width=85, height=22, wrap="word")
    text.pack(fill="both", expand=True)
    message = f"Readable candidates: {report['valid_count']}\n\nBlocked (cannot be approved):\n"
    message += "\n".join(report["failures"]) or "None"
    message += "\n\nProposed safe additions (confirmation follows):\n"
    message += "\n\n".join(label + "\n" + "\n".join(changes) for label, changes in report["repairs"]) or "None"
    text.insert("1.0", message)
    text.configure(state="disabled")
    def choose(value):
        result[0] = value
        window.destroy()
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(10, 0))
    ttk.Button(buttons, text="Cancel", command=lambda: choose(False)).pack(side="right")
    ttk.Button(buttons, text="Continue with valid candidates", command=lambda: choose(True),
               state="normal" if report["valid_count"] else "disabled").pack(side="right", padx=8)
    window.protocol("WM_DELETE_WINDOW", lambda: choose(False))
    window.bind("<Escape>", lambda _event: choose(False))
    previous_grab = parent.grab_current()
    window.grab_set()
    try:
        parent.wait_window(window)
    finally:
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
    return result[0]


def confirm_transfer_conflict(parent, context):
    """Resolve one filename/name/UUID conflict; Cancel is the safe default."""
    import tkinter as tk
    from tkinter import ttk

    result = ["cancel"]
    window = tk.Toplevel(parent)
    window.title("Export conflict" if context["kind"] == "export" else "Profile import conflict")
    window.transient(parent)
    window.resizable(False, False)
    frame = ttk.Frame(window, padding=16)
    frame.grid(sticky="nsew")
    message = context["label"] + "\n\n"
    allowed = context.get("overwrite_allowed", True)
    copy_label = "Save as copy" if context["kind"] == "export" else "Import as copy"
    if context["kind"] == "export":
        message += ("This filename already exists. Overwrite replaces the file. "
                    "Save as copy exports to a free filename ending in (Copy), (Copy 1), etc. "
                    "The profile name and UUID inside the file do not change.")
    else:
        message += "An existing profile has the same " + context["reason"] + ".\n"
        has_id = context.get("has_id", True)
        message += ("Incoming UUID: " + context["incoming"]["id"] + "\n" if has_id else
                    "This legacy record has no embedded UUID.\n")
        message += "Existing: " + "; ".join(f"{item['name']} [{item['id']}]" for item in context["existing"]) + "\n\n"
        message += "Overwrite replaces that profile's settings and name at its current position "
        message += ("and keeps its UUID. " if has_id else "and retains its existing local UUID. ")
        message += ("If another profile already uses the incoming name, (Imported) is appended. "
                    "Import as copy adds an independent copy with a new UUID and a (Copy) name.")
    if not allowed:
        message += ("\n\nOverwrite is unavailable: several profiles share this name, or the destination "
                    f"is not a regular file. Choose Skip or {copy_label}.")
    message += "\n\nSkip leaves this item unchanged. Cancel aborts the entire transfer without changes."
    if context.get("multiple"):
        message += " All applies only to conflicts in this transfer, not to non-conflicting items or future transfers."
    ttk.Label(frame, text=message, wraplength=640, justify="left").grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 16))

    def choose(value):
        result[0] = value
        window.destroy()

    choices = [("Overwrite", "overwrite"), ("Skip", "skip"), (copy_label, "rename")]
    if context.get("multiple"):
        choices += [("Overwrite all", "overwrite_all"), ("Skip all", "skip_all")]
    choices.append(("Cancel", "cancel"))
    for index, (text, value) in enumerate(choices):
        button = ttk.Button(frame, text=text, width=19, command=lambda value=value: choose(value))
        button.grid(row=1 + index // 3, column=index % 3, padx=4, pady=4, sticky="e")
        if not allowed and value.startswith("overwrite"):
            button.state(["disabled"])
        if value == "cancel":
            button.focus_set()
    window.protocol("WM_DELETE_WINDOW", lambda: choose("cancel"))
    window.bind("<Escape>", lambda _event: choose("cancel"))
    previous_grab = parent.grab_current()
    window.grab_set()
    try:
        parent.wait_window(window)
    finally:
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
    return result[0]
