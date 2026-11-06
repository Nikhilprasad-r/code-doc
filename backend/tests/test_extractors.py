def _routes(analysis):
    return {(e.method, e.route, e.handler) for e in analysis.endpoints}


def test_fastapi_routes_apply_router_prefix_and_handle_multiline_decorators(analyzed):
    routes = _routes(analyzed["api/routes/items.py"])
    assert routes == {
        ("GET", "/items/", "read_items"),
        ("POST", "/items/", "create_item"),
        ("DELETE", "/items/{item_id}", "delete_item"),
    }


def test_flask_blueprint_prefix_methods_and_fastapi_mount(analyzed):
    routes = _routes(analyzed["api/main.py"])
    assert ("GET", "/legacy/health", "health") in routes
    assert ("HEAD", "/legacy/health", "health") in routes
    assert ("MOUNT", "{settings.API_V1_STR}", "items.router") in routes


def test_express_and_nestjs_routes(analyzed):
    routes = _routes(analyzed["web/src/server.ts"])
    assert ("GET", "/orders", "listOrders") in routes
    assert ("POST", "/orders", "inline") in routes
    assert ("GET", "/orders/:id", "inline") in routes
    assert ("DELETE", "/orders/:id", "inline") in routes
    assert ("MOUNT", "/api", "router") in routes
    assert ("GET", "/users/:id", "findOne") in routes
    assert ("POST", "/users", "create") in routes
    assert not any(r[1] == "/orders" and r[0] == "GET" and r[2] != "listOrders" for r in routes), \
        "Map.get('/orders') must not be reported as a route"


def test_spring_class_prefix_and_request_mapping_variants(analyzed):
    routes = _routes(analyzed["svc/src/main/java/com/example/petclinic/OwnerController.java"])
    assert routes == {
        ("GET", "/owners/new", "initCreationForm"),
        ("POST", "/owners/new", "processCreationForm"),
        ("GET", "/owners/find", "findOwners"),
        ("GET", "/owners/search", "findOwners"),
        ("GET", "/owners/{ownerId}", "showOwner"),
    }


def test_go_routers(analyzed):
    routes = _routes(analyzed["gosvc/main.go"])
    assert routes == {
        ("GET", "/health", "s.health"),
        ("POST", "/jobs", "s.createJob"),
        ("GET", "/metrics", "metricsHandler"),
    }


def test_dependency_manifests(analyzed):
    deps = {(d.ecosystem, d.name, d.scope) for f in analyzed.values() for d in f.dependencies}
    assert ("pypi", "fastapi", "main") in deps
    assert ("pypi", "pytest", "group:dev") in deps
    assert ("npm", "typescript", "dev") in deps
    assert ("maven", "org.junit.jupiter:junit-jupiter", "test") in deps
    assert ("maven", "org.springframework.boot:spring-boot-starter-parent", "parent") in deps
    assert ("go", "golang.org/x/net", "indirect") in deps


def test_symbols_are_qualified_by_their_container(analyzed):
    qualified = {s.qualified for f in analyzed.values() for s in f.symbols}
    assert {"ItemService.archive", "OwnerController.showOwner", "Server.Routes",
            "UsersController.findOne", "listOrders"} <= qualified


def test_imports(analyzed):
    modules = {m for f in analyzed.values() for m, _ in f.imports}
    assert {"api.deps", "./auth", "github.com/gin-gonic/gin",
            "org.springframework.web.bind.annotation.*"} <= modules
