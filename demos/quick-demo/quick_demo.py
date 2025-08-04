#!/usr/bin/env -S uv run --script

# quick-demo.py: See README.md.

# stdlib imports
import asyncio
import sys

# venv imports
import faker
from pydantic import BaseModel as PydBaseModel

from sqla_pollux import (
    apispec,
    BaseModel,
    create_all,
    dumps,
    loads,
    ModelNotFound,
    new_dbsession,
    populate_module_with_apispec,
    sqla_init,
)
import sqlalchemy as sa
from sqlalchemy.orm import configure_mappers

DBURI = 'sqlite+aiosqlite://'
FAKER = faker.Faker()

######################################################################
# Model definition
######################################################################
_AppUserTable = sa.Table(
    "appuser",
    BaseModel.metadata,

    # id is read-only
    sa.Column("id", sa.Integer, primary_key=True,
              info=apispec.READ_ONLY()),
    # username cannot be updated
    sa.Column("username", sa.String(255), nullable=False, unique=True,
              info=apispec.NO_UPDATE()),
    # fullname is available for CRU, which is the default if apispec is not specified
    sa.Column("fullname", sa.String(255), nullable=False),
    # password can be set on create
    sa.Column("password", sa.TEXT, nullable=False, info=apispec.CREATE_ONLY()),
    # created is read-only
    sa.Column("created", sa.DateTime(timezone=True), info=apispec.READ_ONLY(),
              server_default=sa.text("CURRENT_TIMESTAMP")),
    # lastlogin is read-only
    sa.Column("lastlogin", sa.DateTime(timezone=True), info=apispec.READ_ONLY()),
    # metrics is not available and so marked INTERNAL
    sa.Column("metrics", sa.JSON, default=dict, server_default=sa.text("'{}'"),
              info=apispec.INTERNAL()),
)

class AppUser(BaseModel):
      """Application User"""

      __table__ = _AppUserTable

configure_mappers()

# populates this module with shortcuts to the pydantic generated
# models on AppUser.apispec: AppUserCreate, AppUserRead, AppUserUpdate
populate_module_with_apispec(sys.modules[__name__])


######################################################################
# Mocking REST Server
######################################################################

def gen_user_data():
   """Generate data suitable for creation of an AppUser"""
   return dict(
      username=FAKER.user_name(),
      fullname=FAKER.name(),
      password=FAKER.password(),
   )

class MockRestApi:
   """Mock a REST API framework

   Each HTTP method will be processed by the appropriate do_METHOD
   method. Each of these methods has are annotated to aid in
   processing request data.

   A "request" will be handled by a call to handler(). See that method.

   """

   async def handler(self, method, *args, **data):
      """Mock pre-request handling

      Args:
         method (str): an HTTP method, PUT, GET, also collection_GET, collection_POST
         *args, **data: passed to method

      NOTE: about **data - imagine we received a JSON object as the
            body and ran json.loads() on it. This is all the data
            found in the body of the document, so there's only one
            pydantic model for each method.

      Returns - Any - the output of the do_METHOD()
      """
      # find the do_METHOD
      meth = getattr(self, f'do_{method.lower()}', None)
      if meth is None:
         raise ValueError('Bad request')

      # using the annotations, convert any Pydantic BaseModel with the
      # given data to create an instance of the model.
      model = dict()
      for key, val in meth.__annotations__.items():
         if key == 'return':
            continue
         if isinstance(val, type) and issubclass(val, PydBaseModel):
            model[key] = val(**data)
            break

      # we'll mock that each request happens within its own sqla
      # session as it would with most web frameworks, e.g., via
      # middleware
      with new_dbsession() as sess:
         async with sess:
            async with sess.begin():
               return await meth(*args, **model)

   async def do_collection_get(self) -> list[AppUserRead]:
      """mock GET /appuser"""
      users = await AppUser.list()
      return dumps(users)

   async def do_collection_post(self, appuser: AppUserCreate) -> AppUserRead:
      """mock POST /appuser"""
      data = appuser.model_dump()

      user = await AppUser.create(**data)
      return dumps(user)

   async def do_get(self, uid: int, /) -> AppUserRead:
      """mock GET /appuser/{id}"""
      user = await AppUser.get(uid)
      if not user:
         raise ModelNotFound

      return dumps(user)

   async def do_put(self, uid: int, /, appuser: AppUserUpdate) -> AppUserRead:
      """mock PUT /appuser/{id}"""
      user = await AppUser.get(uid)
      if not user:
         raise ModelNotFound

      data = appuser.model_dump()

      user = await user.update(**data)
      return dumps(user)

   async def do_delete(self, uid: int, /) -> None:
      """mock DELETE /appuser/{id}"""
      user = await AppUser.get(uid)
      if not user:
         raise ModelNotFound

      await user.delete()

      return None


async def main():
   """Main entry point of demo"""


   engine = sqla_init(DBURI, echo=True)

   await create_all()

   api = MockRestApi()

   ########################################
   # Create 10 users
   ########################################
   requests = [api.handler('COLLECTION_POST', **gen_user_data())
               for _x in range(10)]

   ########################################
   # convert to dict, keyed on id
   ########################################
   users = dict((user['id'], user)
                for user in (
                      loads(u) for u in await asyncio.gather(*requests)))

   ########################################
   # test when we do a COLLECTION_GET we get back the same users
   ########################################
   refetched = dict((user['id'], user)
                    for user in loads(await api.handler('COLLECTION_GET')))

   assert users == refetched

   ########################################
   # test indiviual GETs return the users
   ########################################
   for uid, user in users.items():
      assert user == loads(await api.handler('GET', uid))

   ########################################
   # update a user
   ########################################
   # grab random user
   uid = FAKER.random_element(users.keys())
   # update their fullname
   user = users[uid]
   oldname = user['fullname']
   while oldname == user['fullname']:
      user['fullname'] = newname = FAKER.name()

   # do the PUT and confirm
   updated = loads(await api.handler('PUT', uid, **user))
   assert updated['fullname'] == newname

   ########################################
   # test ModelNotFound
   ########################################
   for method in 'GET', 'PUT', 'DELETE':
      try:
         data = {"fullname": "phred"} if method == 'PUT' else {}
         await api.handler(method, 999999, **data)
      except ModelNotFound:
         pass

   ########################################
   # test deleting
   ########################################
   for uid in users:
      await api.handler('DELETE', uid)

   ########################################
   # confirm no users left
   ########################################
   users = loads(await api.handler('COLLECTION_GET'))
   assert not users


if __name__ == '__main__':
   try:
      asyncio.run(main())
   except Exception as exc:
      print(f'An error occurred: {exc}', file=sys.stderr)
      sys.exit(1)
