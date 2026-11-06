"""Register annotation review, dataset export routes and the retained history schema."""

from . import training_export
from .annotation_review_routes import router as annotation_review_router
from .db import connect
from .review_schema import apply_review_schema
from .training_export_routes import legacy_router
from .training_export_routes import router as dataset_router


def register_review_routers(app) -> None:
    """Install annotation review and dataset routers.

    The A/B/C/F review and confirmation tables stay migrated as read-only history
    for statistics and audit exports; their pages and HTTP routes were removed.
    """
    from .review_logging import configure_review_logging

    configure_review_logging()
    app.include_router(annotation_review_router)
    app.include_router(dataset_router)
    app.include_router(legacy_router)
    from .media_library import router as library_router

    app.include_router(library_router)
    from .db import _DB_LOCK

    with _DB_LOCK, connect() as conn:
        apply_review_schema(conn)
        from .review_workflow import migrate

        migrate(conn)
        from .confirmation_workflow import migrate as migrate_confirmation

        migrate_confirmation(conn)
        training_export.migrate(conn)
        from .annotation_review import migrate as migrate_annotation_review

        migrate_annotation_review(conn)
        conn.commit()
