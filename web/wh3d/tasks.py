"""Zadania Celery modułu Magazyn 3D (autodiscover: palletweb.celery). W DEBUG — eagerly."""
from celery import shared_task


@shared_task(name="wh3d.import_warehouse_tasks")
def import_warehouse_tasks(batch_id, token):
    """Import dużego eksportu zadań magazynowych EWM poza żądaniem HTTP."""
    from .ewm_tasks_import import run_import

    run_import(batch_id, token)
