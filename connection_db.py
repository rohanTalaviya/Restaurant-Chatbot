from pymongo import MongoClient

MONGO_URI = "mongodb://fitshield_ro:F!t%24h!3lD_Pr0D_ro@ec2-13-204-93-167.ap-south-1.compute.amazonaws.com:17018/?authMechanism=SCRAM-SHA-256&authSource=Fitshield"
client = MongoClient(MONGO_URI)
db = client["Fitshield"]

RestaurantMenuData = db["RestaurantMenuData"]
RestroData = db["RestroData"]