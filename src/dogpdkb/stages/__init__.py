"""Stage handler packages for the unified batch runner.

Each module ``dogpdkb.stages.<stage>`` exposes ``HANDLER``, a callable taking a
``StageContext`` and returning a ``StageResult``. ``sync_stages.load_handlers``
imports whatever is installed; a missing module means the stage is unregistered.
"""
