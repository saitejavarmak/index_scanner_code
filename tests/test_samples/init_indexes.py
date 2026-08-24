from pymongo import MongoClient, IndexModel, ASCENDING, DESCENDING

db = MongoClient()["mydb"]

# Single field index
db.users.create_index("email", unique=True)

# Compound index
db.users.create_index([("tenantId", ASCENDING), ("createdAt", DESCENDING)])

# Using IndexModel
indexes = [
    IndexModel([("status", ASCENDING)]),
    IndexModel([("name", ASCENDING), ("email", ASCENDING)], unique=True),
]
db.users.create_indexes(indexes)
