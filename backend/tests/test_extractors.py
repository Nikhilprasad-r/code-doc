from app.ingest.symbols import SIGNATURE_MAX, extract_symbols, parse


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


def test_signatures_show_the_declaration_after_its_annotations(analyzed):
    signatures = {s.qualified: s.signature for f in analyzed.values() for s in f.symbols}
    # Java annotations live inside the declaration; the declaration itself used to be dropped.
    assert signatures["OwnerController.showOwner"] == \
        '@GetMapping("/{ownerId}") public ModelAndView showOwner(@PathVariable("ownerId") int ownerId)'
    assert signatures["OwnerController"] == '@Controller @RequestMapping("/owners") class OwnerController'
    # TypeScript decorators sit before the declaration, in the class body or the export statement.
    assert signatures["UsersController.findOne"] == '@Get(":id") findOne(id: string)'
    assert signatures["UsersController"] == '@Controller("users") class UsersController'
    # Python: multi-line decorators and parameters are joined onto one line.
    assert signatures["create_item"] == ('@router.post("/", response_model=Item) async def create_item(*, '
                                         'session: SessionDep, item_in: ItemCreate, '
                                         'user=Depends(get_current_user)) -> Item')
    assert signatures["ItemService.archive"] == "def archive(self, item_id: int) -> None"
    assert signatures["Server"] == "Server struct"
    assert signatures["Server.Routes"] == "func (s *Server) Routes()"
    assert signatures["listOrders"] == "listOrders = async (req, res)"


def test_long_annotations_are_shortened_before_the_declaration():
    source = b'''interface OwnerRepository extends Repository<Owner, Integer> {
\t@Query("SELECT DISTINCT owner FROM Owner owner left join owner.pets WHERE owner.lastName LIKE :lastName% '''
    source += b'''ORDER BY owner.lastName, owner.firstName, owner.id, owner.address, owner.city, owner.telephone")
\t@Transactional(readOnly = true)
\tPage<Owner> findByLastName(
\t\t\t@Param("lastName") String lastName,
\t\t\tPageable pageable);
}
'''
    symbols = {s.qualified: s.signature for s in extract_symbols("java", parse("java", source), source)}
    assert symbols["OwnerRepository"] == "interface OwnerRepository extends Repository<Owner, Integer>"
    method = symbols["OwnerRepository.findByLastName"]
    assert method.startswith('@Query("SELECT DISTINCT owner')
    assert method.endswith('… Page<Owner> findByLastName(@Param("lastName") String lastName, Pageable pageable)')
    assert len(method) <= SIGNATURE_MAX
