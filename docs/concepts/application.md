# Application

`Application` owns one process's Core configuration, service registry, container, events, lifecycle, and inspectable state. Register services only during `created`; call `configure`, `initialize`, `start`, and `stop` in order, or use the ASGI lifespan bridge.
