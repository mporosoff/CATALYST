"""Synthetic native batch controls: review-only gestures and send-state gating."""
from dataclasses import replace
from pathlib import Path
import sys
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from catalyst_desktop.batch import BatchQueue
from catalyst_desktop.batch_gui import BatchPanel
from test_batch import record, approve


def main():
    root = tk.Tk()
    root.withdraw()
    calls = []
    panel = BatchPanel(root, {key: lambda *args, action=key: calls.append((action, args))
        for key in ('inspect', 'edit', 'approve', 'remove', 'move_up', 'move_down', 'send')})
    panel.pack()
    queue = BatchQueue()
    revision, sources = record()
    item = queue.add(revision, sources)
    panel.refresh(queue.items)
    root.update_idletasks()
    assert panel.selected_id() == item.id
    assert panel._buttons['approve'].instate(['!disabled'])
    assert panel._buttons['send'].instate(['disabled'])
    panel._invoke('inspect')
    assert calls == [('inspect', (item.id,))]
    queue.approve(item.id, approve(revision))
    panel.refresh(queue.items)
    assert panel._buttons['send'].instate(['!disabled'])
    panel.set_busy(True)
    assert all(button.instate(['disabled']) for button in panel._buttons.values())
    panel.set_busy(False)
    failed = replace(queue.get(item.id), status='needs_attention')
    panel.refresh((failed,))
    assert panel._buttons['inspect'].instate(['!disabled'])
    assert all(panel._buttons[key].instate(['disabled']) for key in ('edit', 'remove', 'approve', 'send'))
    panel.refresh(())
    assert panel.selected_id() is None
    assert all(button.instate(['disabled']) for button in panel._buttons.values())
    root.destroy()
    print('Batch panel: review selection, approval gating, busy state, and transfer-failure controls passed.')


if __name__ == '__main__':
    main()
