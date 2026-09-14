from orbit import Application, ApplicationConfig, Service, ServiceDescriptor
from orbit.asgi import Response
from orbit.runtime import Runtime


class GreetingService(Service):
  descriptor = ServiceDescriptor(name="greeting")

  async def health(self):
      from orbit.health import HealthReport, HealthStatus

      return HealthReport(status=HealthStatus.HEALTHY)


application = Application(ApplicationConfig(name="hello"))

application.register(GreetingService())


@application.router.route("/", method="GET", name="home")
async def home(request):
  return Response.json({"message": "Hello from Orbit"})

@application.router.route("/hello/{name}", method="GET", name="hello")
async def hello(request):
  return Response.json(
      {
          "name": request.path_parameters["name"],
          "query": request.query_parameters,
      }
  )
  
runtime = Runtime(application)
