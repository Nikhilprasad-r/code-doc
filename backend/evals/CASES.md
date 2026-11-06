# Eval questions (draft for review)

40 questions over two repositories at pinned commits. Each has key facts the answer must state and
gold locations (`path:start-end`) where the evidence lives. `items` questions are graded by checking
that each listed route/package appears in the answer; `judge` questions by claude-sonnet-5-5 marking
each key fact as stated correctly or not. `absent` questions test that the assistant says a feature
does not exist instead of inventing one.

| Repository | Commit |
|---|---|
| spring-projects/spring-petclinic | `500158f732419217507c7656904b8e6aa1bcc0d6` |
| fastapi/full-stack-fastapi-template | `aabb90126964a164e02aff39e7c385bb0598236d` |

| Type | spring-petclinic | full-stack-fastapi-template | total |
|---|---|---|---|
| overview | 2 | 2 | 4 |
| where | 6 | 5 | 11 |
| how | 5 | 6 | 11 |
| endpoints | 2 | 3 | 5 |
| dependencies | 3 | 2 | 5 |
| absent | 2 | 2 | 4 |

| id | type | grader | question |
|---|---|---|---|
| pc-01 | overview | judge | Give me an overview of how this application is structured. |
| pc-02 | overview | judge | Which database does the app use by default, and how do you switch it to MySQL or PostgreSQL? |
| pc-03 | where | judge | Where is the owner search by last name implemented? |
| pc-04 | where | judge | Where are pet form inputs validated, and what rules are enforced? |
| pc-05 | where | judge | Where and how is the list of vets cached? |
| pc-06 | where | judge | How is the UI language chosen, and how can a user switch it? |
| pc-07 | where | judge | Which code converts the pet type chosen in the form into a PetType object? |
| pc-08 | how | judge | Walk me through what happens when a user books a new visit for a pet. |
| pc-09 | how | judge | How does the app prevent an owner from having two pets with the same name? |
| pc-10 | how | judge | When an owner's details are updated, how does the code guard against editing the wrong owner? |
| pc-11 | how | judge | What is the difference between the /vets and /vets.html endpoints? |
| pc-12 | how | judge | What happens when a request hits /oups? |
| pc-13 | endpoints | items | List every HTTP endpoint this application exposes, with its HTTP method. |
| pc-14 | endpoints | judge | Which endpoints create and edit pets, and where does their URL prefix come from? |
| pc-15 | dependencies | items | Which database drivers does the Maven build declare? |
| pc-16 | dependencies | judge | Which Spring Boot version and Java version does the project target? |
| pc-17 | dependencies | judge | Which libraries provide caching in this project? |
| pc-18 | where | judge | Where is the number of owners shown per page in search results defined? |
| pc-19 | absent | judge | How does the application authenticate users and restrict access to admin pages? |
| pc-20 | absent | judge | Which endpoint deletes an owner, and what happens to their pets? |
| fa-01 | overview | judge | Give me an overview of the backend architecture. |
| fa-02 | overview | judge | How does the frontend call the backend API, and where is the auth token attached to requests? |
| fa-03 | where | judge | Where is a user's password checked when they log in? |
| fa-04 | where | judge | Where is the JWT access token created, and how long is it valid? |
| fa-05 | where | judge | Where and when is the first superuser account created? |
| fa-06 | where | judge | Which dependency restricts endpoints to superusers, and which endpoints use it? |
| fa-07 | how | judge | Explain the password reset flow end to end. |
| fa-08 | how | judge | How does login avoid revealing through response time whether an email is registered? |
| fa-09 | how | judge | How are password hashes upgraded when the hashing scheme changes? |
| fa-10 | how | judge | When listing items, which items does each kind of user see? |
| fa-11 | how | judge | What happens to a user's items when the user is deleted? |
| fa-12 | how | judge | How does the backend stop a deployment from running with default secrets? |
| fa-13 | endpoints | items | List all backend API endpoints with their HTTP methods and full paths. |
| fa-14 | endpoints | judge | Which API endpoints can be called without being logged in? |
| fa-15 | endpoints | judge | Is there an endpoint that creates users without authentication, and when is it enabled? |
| fa-16 | dependencies | items | Which libraries does the backend use for JWTs, password hashing, the ORM, and database migrations? |
| fa-17 | dependencies | judge | Which PostgreSQL driver does the backend use, and how is the database URL adapted for it? |
| fa-18 | where | judge | Where is the backend health check defined, and what uses it? |
| fa-19 | absent | judge | Where is rate limiting on the login endpoint implemented? |
| fa-20 | absent | judge | How does the backend use Redis for caching? |

## pc-01 · spring-projects/spring-petclinic · overview

**Q:** Give me an overview of how this application is structured.

**Key facts:**
- It is a Spring Boot web application using Spring MVC controllers that render Thymeleaf templates.
- Code is organised by feature into packages such as owner, vet, system and model.
- Persistence uses Spring Data JPA repositories (e.g. OwnerRepository, VetRepository, PetTypeRepository).
- It uses an H2 database by default, with MySQL and PostgreSQL available as alternatives.

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java:47-56`, `src/main/java/org/springframework/samples/petclinic/owner/OwnerRepository.java:36-62`, `src/main/resources/application.properties:1-4`

## pc-02 · spring-projects/spring-petclinic · overview

**Q:** Which database does the app use by default, and how do you switch it to MySQL or PostgreSQL?

**Key facts:**
- The default database is H2 (database=h2 in application.properties).
- Schema and data scripts are chosen per database via db/${database}/schema.sql and data.sql.
- MySQL or PostgreSQL is selected with a Spring profile (application-mysql.properties / application-postgres.properties) that sets the database and datasource URL.

**Gold locations:** `src/main/resources/application.properties:1-4`, `src/main/resources/application-mysql.properties:1-7`

## pc-03 · spring-projects/spring-petclinic · where

**Q:** Where is the owner search by last name implemented?

**Key facts:**
- OwnerController.processFindForm handles GET /owners.
- It calls OwnerRepository.findByLastNameStartingWith (prefix match on last name) with paging.
- When exactly one owner matches it redirects to that owner's details page.
- When nothing matches it rejects the lastName field with a 'notFound' error and shows the find form again.

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java:94-146`, `src/main/java/org/springframework/samples/petclinic/owner/OwnerRepository.java:45-45`

## pc-04 · spring-projects/spring-petclinic · where

**Q:** Where are pet form inputs validated, and what rules are enforced?

**Key facts:**
- PetValidator.validate checks that the name is present and at most 30 characters.
- PetValidator requires a type for new pets and requires a birth date.
- PetValidator is registered for the 'pet' model attribute via @InitBinder in PetController.
- PetController additionally rejects birth dates in the future and duplicate pet names for the owner.

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/owner/PetValidator.java:39-59`, `src/main/java/org/springframework/samples/petclinic/owner/PetController.java:94-98`, `src/main/java/org/springframework/samples/petclinic/owner/PetController.java:111-118`

## pc-05 · spring-projects/spring-petclinic · where

**Q:** Where and how is the list of vets cached?

**Key facts:**
- VetRepository's findAll methods are annotated @Cacheable("vets").
- CacheConfiguration enables caching (@EnableCaching) and creates the 'vets' cache through a JCacheManagerCustomizer.
- The cache is configured with statistics enabled.

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/vet/VetRepository.java:44-56`, `src/main/java/org/springframework/samples/petclinic/system/CacheConfiguration.java:31-51`

## pc-06 · spring-projects/spring-petclinic · where

**Q:** How is the UI language chosen, and how can a user switch it?

**Key facts:**
- WebConfiguration defines a SessionLocaleResolver with English as the default locale.
- A LocaleChangeInterceptor switches the language from the 'lang' request parameter (e.g. ?lang=de).
- Translations come from messages/messages*.properties files.

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/system/WebConfiguration.java:32-58`, `src/main/resources/application.properties:16-16`

## pc-07 · spring-projects/spring-petclinic · where

**Q:** Which code converts the pet type chosen in the form into a PetType object?

**Key facts:**
- PetTypeFormatter (a Spring Formatter<PetType>) does the conversion.
- Its parse method loads all types with PetTypeRepository.findPetTypes and returns the one whose name matches the text.
- If no type matches it throws a ParseException ('type not found').

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/owner/PetTypeFormatter.java:36-60`

## pc-08 · spring-projects/spring-petclinic · how

**Q:** Walk me through what happens when a user books a new visit for a pet.

**Key facts:**
- The form posts to /owners/{ownerId}/pets/{petId}/visits/new, handled by VisitController.processNewVisitForm.
- Before the handler runs, the @ModelAttribute method loadPetWithVisit loads the owner and pet (failing if either is missing) and attaches a new Visit to the pet.
- A visit date that is not after today is rejected.
- On success it calls owner.addVisit(petId, visit), saves the owner, and redirects to the owner page with a 'Your visit has been booked' message.

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/owner/VisitController.java:63-112`, `src/main/java/org/springframework/samples/petclinic/owner/Owner.java:176-186`

## pc-09 · spring-projects/spring-petclinic · how

**Q:** How does the app prevent an owner from having two pets with the same name?

**Key facts:**
- When adding a pet, processCreationForm checks owner.getPet(name, true) and rejects the name as 'duplicate'.
- The name comparison is case-insensitive (equalsIgnoreCase in Owner.getPet).
- The database also has a unique constraint unique_owner_pet_name on (owner_id, name).
- A DataIntegrityViolationException from that constraint is caught and turned into the same duplicate-name error.

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/owner/PetController.java:107-137`, `src/main/java/org/springframework/samples/petclinic/owner/PetController.java:202-205`, `src/main/java/org/springframework/samples/petclinic/owner/Owner.java:147-157`, `src/main/resources/db/h2/schema.sql:55-55`

## pc-10 · spring-projects/spring-petclinic · how

**Q:** When an owner's details are updated, how does the code guard against editing the wrong owner?

**Key facts:**
- processUpdateOwnerForm compares the owner id from the form with the ownerId path variable.
- On a mismatch it rejects the 'id' field and redirects back to the edit form with an error message.
- The @InitBinder in OwnerController disallows binding the id fields ('id', '*.id') from request parameters.

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java:153-171`, `src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java:59-62`

## pc-11 · spring-projects/spring-petclinic · how

**Q:** What is the difference between the /vets and /vets.html endpoints?

**Key facts:**
- /vets.html renders the vets/vetList HTML page, paginated 5 vets per page.
- /vets returns a Vets object as the response body (@ResponseBody) for JSON/XML clients, containing all vets.
- Both read vets through VetRepository.

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/vet/VetController.java:45-79`

## pc-12 · spring-projects/spring-petclinic · how

**Q:** What happens when a request hits /oups?

**Key facts:**
- CrashController handles GET /oups and deliberately throws a RuntimeException.
- It exists to showcase error handling; the error is rendered by the error.html template.

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/system/CrashController.java:28-35`

## pc-13 · spring-projects/spring-petclinic · endpoints

**Q:** List every HTTP endpoint this application exposes, with its HTTP method.

**Expected items:**
- /owners/new
- /owners/find
- /owners/{ownerId}/edit
- /owners/{ownerId}/pets/new
- /owners/{ownerId}/pets/{petId}/edit
- /owners/{ownerId}/pets/{petId}/visits/new
- /vets.html
- /vets
- /oups

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java:72-186`, `src/main/java/org/springframework/samples/petclinic/owner/PetController.java:100-145`, `src/main/java/org/springframework/samples/petclinic/owner/VisitController.java:90-98`, `src/main/java/org/springframework/samples/petclinic/vet/VetController.java:45-73`, `src/main/java/org/springframework/samples/petclinic/system/CrashController.java:31-31`

## pc-14 · spring-projects/spring-petclinic · endpoints

**Q:** Which endpoints create and edit pets, and where does their URL prefix come from?

**Key facts:**
- GET and POST /owners/{ownerId}/pets/new create a pet.
- GET and POST /owners/{ownerId}/pets/{petId}/edit edit a pet.
- The /owners/{ownerId} prefix comes from the class-level @RequestMapping on PetController.

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/owner/PetController.java:47-49`, `src/main/java/org/springframework/samples/petclinic/owner/PetController.java:100-145`

## pc-15 · spring-projects/spring-petclinic · dependencies

**Q:** Which database drivers does the Maven build declare?

**Expected items:**
- h2
- mysql-connector-j
- postgresql

**Gold locations:** `pom.xml:76-95`

## pc-16 · spring-projects/spring-petclinic · dependencies

**Q:** Which Spring Boot version and Java version does the project target?

**Key facts:**
- The Maven parent is spring-boot-starter-parent version 4.1.0.
- The Java version is 17.

**Gold locations:** `pom.xml:6-9`, `pom.xml:19-19`

## pc-17 · spring-projects/spring-petclinic · dependencies

**Q:** Which libraries provide caching in this project?

**Key facts:**
- spring-boot-starter-cache provides Spring's caching support.
- javax.cache:cache-api provides the JCache (JSR-107) API.
- Caffeine is the runtime cache provider.

**Gold locations:** `pom.xml:47-49`, `pom.xml:67-70`, `pom.xml:81-85`

## pc-18 · spring-projects/spring-petclinic · where

**Q:** Where is the number of owners shown per page in search results defined?

**Key facts:**
- It is pageSize = 5 in OwnerController.findPaginatedForOwnersLastName.

**Gold locations:** `src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java:141-146`

## pc-19 · spring-projects/spring-petclinic · absent

**Q:** How does the application authenticate users and restrict access to admin pages?

**Key facts:**
- States that the application has no authentication or authorization: there is no login and no Spring Security dependency or configuration.

**Gold locations:** none (feature is absent)

## pc-20 · spring-projects/spring-petclinic · absent

**Q:** Which endpoint deletes an owner, and what happens to their pets?

**Key facts:**
- States that there is no endpoint for deleting owners (owners can only be created, searched, viewed and edited).

**Gold locations:** none (feature is absent)

## fa-01 · fastapi/full-stack-fastapi-template · overview

**Q:** Give me an overview of the backend architecture.

**Key facts:**
- backend/app/main.py creates the FastAPI app and includes api_router under settings.API_V1_STR (/api/v1).
- Routes are split into login, users, utils, items and private modules, and the private routes are only included in development.
- Data models are SQLModel classes in models.py stored in PostgreSQL, with Alembic migrations.
- Configuration comes from a pydantic-settings Settings class in core/config.py.

**Gold locations:** `backend/app/main.py:21-36`, `backend/app/api/main.py:1-14`, `backend/app/core/config.py:15-31`

## fa-02 · fastapi/full-stack-fastapi-template · overview

**Q:** How does the frontend call the backend API, and where is the auth token attached to requests?

**Key facts:**
- The frontend uses a generated API client in frontend/src/client.
- frontend/src/main.tsx configures the client's baseURL from VITE_API_URL and its auth from the access_token stored in localStorage.
- On 401 or 403 responses the token is removed and the user is redirected to /login.

**Gold locations:** `frontend/src/main.tsx:17-30`, `frontend/src/hooks/useAuth.ts:41-46`

## fa-03 · fastapi/full-stack-fastapi-template · where

**Q:** Where is a user's password checked when they log in?

**Key facts:**
- POST /login/access-token (login_access_token in api/routes/login.py) handles login.
- It calls crud.authenticate, which looks the user up by email and calls verify_password.
- A wrong email or password returns 400 'Incorrect email or password'; an inactive user gets 400 'Inactive user'.

**Gold locations:** `backend/app/api/routes/login.py:23-42`, `backend/app/crud.py:45-60`, `backend/app/core/security.py:29-32`

## fa-04 · fastapi/full-stack-fastapi-template · where

**Q:** Where is the JWT access token created, and how long is it valid?

**Key facts:**
- core/security.py create_access_token encodes 'exp' and 'sub' claims with SECRET_KEY using HS256.
- Validity is ACCESS_TOKEN_EXPIRE_MINUTES, which defaults to 60 * 24 * 8 minutes (8 days).
- login_access_token creates the token for the authenticated user's id.

**Gold locations:** `backend/app/core/security.py:19-26`, `backend/app/core/config.py:24-25`, `backend/app/api/routes/login.py:37-42`

## fa-05 · fastapi/full-stack-fastapi-template · where

**Q:** Where and when is the first superuser account created?

**Key facts:**
- init_db in core/db.py creates a superuser from FIRST_SUPERUSER and FIRST_SUPERUSER_PASSWORD if no user with that email exists.
- init_db is called from app/initial_data.py.
- scripts/prestart.sh runs 'alembic upgrade head' and then initial_data.py.

**Gold locations:** `backend/app/core/db.py:15-33`, `backend/app/initial_data.py:11-18`, `backend/scripts/prestart.sh:6-10`

## fa-06 · fastapi/full-stack-fastapi-template · where

**Q:** Which dependency restricts endpoints to superusers, and which endpoints use it?

**Key facts:**
- get_current_active_superuser in api/deps.py raises 403 ('The user doesn't have enough privileges') if the current user is not a superuser.
- It protects the user-administration endpoints: listing users, creating users, and updating or deleting a user by id.
- It also protects POST /utils/test-email/ and POST /password-recovery-html-content/{email}.

**Gold locations:** `backend/app/api/deps.py:52-57`, `backend/app/api/routes/users.py:32-36`, `backend/app/api/routes/utils.py:11-15`, `backend/app/api/routes/login.py:100-104`

## fa-07 · fastapi/full-stack-fastapi-template · how

**Q:** Explain the password reset flow end to end.

**Key facts:**
- POST /password-recovery/{email} creates a reset token and emails it only if the user exists, but always returns the same message (to avoid revealing which emails are registered).
- The reset token is a JWT whose subject is the email and which expires after EMAIL_RESET_TOKEN_EXPIRE_HOURS (48 hours by default).
- POST /reset-password/ verifies the token with verify_password_reset_token, loads the user, and updates the password via crud.update_user.
- An invalid token and an unknown user both return 400 'Invalid token'; an inactive user is rejected.

**Gold locations:** `backend/app/api/routes/login.py:53-97`, `backend/app/utils.py:105-125`

## fa-08 · fastapi/full-stack-fastapi-template · how

**Q:** How does login avoid revealing through response time whether an email is registered?

**Key facts:**
- When no user has that email, crud.authenticate still calls verify_password against a fixed DUMMY_HASH, so the response takes about as long as a real check.

**Gold locations:** `backend/app/crud.py:42-51`

## fa-09 · fastapi/full-stack-fastapi-template · how

**Q:** How are password hashes upgraded when the hashing scheme changes?

**Key facts:**
- core/security.py configures pwdlib PasswordHash with Argon2 (preferred) and Bcrypt.
- verify_password uses verify_and_update, which returns a new hash when the stored one uses an outdated scheme.
- crud.authenticate saves the updated hash on the user when one is returned.

**Gold locations:** `backend/app/core/security.py:11-16`, `backend/app/core/security.py:29-32`, `backend/app/crud.py:52-59`

## fa-10 · fastapi/full-stack-fastapi-template · how

**Q:** When listing items, which items does each kind of user see?

**Key facts:**
- GET /items/ (read_items) returns all items for superusers.
- Other users only get items whose owner_id is their own id.
- Results are ordered by created_at descending, paginated with skip/limit, and include a total count.

**Gold locations:** `backend/app/api/routes/items.py:13-45`

## fa-11 · fastapi/full-stack-fastapi-template · how

**Q:** What happens to a user's items when the user is deleted?

**Key facts:**
- Their items are deleted too: User.items is a Relationship with cascade_delete=True.
- Item.owner_id is a foreign key to user.id with ondelete='CASCADE'.
- Superusers are not allowed to delete themselves (403).

**Gold locations:** `backend/app/models.py:51-55`, `backend/app/models.py:87-93`, `backend/app/api/routes/users.py:132-143`

## fa-12 · fastapi/full-stack-fastapi-template · how

**Q:** How does the backend stop a deployment from running with default secrets?

**Key facts:**
- A Settings model validator checks SECRET_KEY, the database password and FIRST_SUPERUSER_PASSWORD for the value 'changethis'.
- Outside development it raises a ValueError; with FASTAPI_ENV=development it only issues a warning.

**Gold locations:** `backend/app/core/config.py:68-88`

## fa-13 · fastapi/full-stack-fastapi-template · endpoints

**Q:** List all backend API endpoints with their HTTP methods and full paths.

**Expected items:**
- /api/v1
- /login/access-token
- /login/test-token
- /password-recovery/{email}
- /reset-password/
- /password-recovery-html-content/{email}
- /users/signup
- /users/me/password
- /users/{user_id}
- /utils/test-email/
- /utils/health-check/
- /items/{id}
- /private/users/

**Gold locations:** `backend/app/api/routes/login.py:23-104`, `backend/app/api/routes/users.py:32-215`, `backend/app/api/routes/items.py:13-100`, `backend/app/api/routes/utils.py:11-30`, `backend/app/api/routes/private.py:23-23`, `backend/app/main.py:35-35`

## fa-14 · fastapi/full-stack-fastapi-template · endpoints

**Q:** Which API endpoints can be called without being logged in?

**Key facts:**
- POST /login/access-token and POST /users/signup are public.
- POST /password-recovery/{email} and POST /reset-password/ are public.
- GET /utils/health-check/ is public.

**Gold locations:** `backend/app/api/routes/login.py:23-97`, `backend/app/api/routes/users.py:146-159`, `backend/app/api/routes/utils.py:29-31`

## fa-15 · fastapi/full-stack-fastapi-template · endpoints

**Q:** Is there an endpoint that creates users without authentication, and when is it enabled?

**Key facts:**
- Yes: POST /private/users/ in api/routes/private.py creates a user with no authentication dependency.
- The private router is only included when FASTAPI_ENV is 'development'.

**Gold locations:** `backend/app/api/routes/private.py:13-38`, `backend/app/api/main.py:13-14`

## fa-16 · fastapi/full-stack-fastapi-template · dependencies

**Q:** Which libraries does the backend use for JWTs, password hashing, the ORM, and database migrations?

**Expected items:**
- pyjwt
- pwdlib
- sqlmodel
- alembic

**Gold locations:** `backend/pyproject.toml:6-21`

## fa-17 · fastapi/full-stack-fastapi-template · dependencies

**Q:** Which PostgreSQL driver does the backend use, and how is the database URL adapted for it?

**Key facts:**
- The driver is psycopg (version 3, psycopg[binary]).
- A Settings field validator rewrites postgres:// or postgresql:// URLs to postgresql+psycopg://.

**Gold locations:** `backend/app/core/config.py:33-40`, `backend/pyproject.toml:6-21`

## fa-18 · fastapi/full-stack-fastapi-template · where

**Q:** Where is the backend health check defined, and what uses it?

**Key facts:**
- GET /api/v1/utils/health-check/ in api/routes/utils.py returns True.
- The backend service's Docker Compose healthcheck curls that URL.

**Gold locations:** `backend/app/api/routes/utils.py:29-31`, `compose.yml:67-68`

## fa-19 · fastapi/full-stack-fastapi-template · absent

**Q:** Where is rate limiting on the login endpoint implemented?

**Key facts:**
- States that the codebase does not implement rate limiting for login (no limiter middleware, dependency or proxy rule).

**Gold locations:** none (feature is absent)

## fa-20 · fastapi/full-stack-fastapi-template · absent

**Q:** How does the backend use Redis for caching?

**Key facts:**
- States that the backend does not use Redis or any caching layer (no Redis dependency or service).

**Gold locations:** none (feature is absent)
