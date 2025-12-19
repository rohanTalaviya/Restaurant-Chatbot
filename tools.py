from langchain_core.tools import tool
import json

from menu_processing import get_clean_dish_data, get_clean_dish_data_one, get_dish_data
from connection_db import RestaurantMenuData

@tool
def get_menu_data(restaurant_id):
    """Get menu data for a specific restaurant."""
    print("called get_menu_data")
    menu = RestaurantMenuData.find_one({"_id": restaurant_id})
    if not menu_data or "menu" not in menu_data:
        return json.dumps({"error": "Menu data not found."})
    menu = menu["menu"]
    # Pass the entire list to get_clean_dish_data
    final_menu = get_clean_dish_data(menu)
    return json.dumps(final_menu, default=str)

@tool
def dish_data(dish_name: str, restaurant_id: str):
    """
    Get detailed information about a specific dish.
    Use this tool when the user asks about nutritional value, ingredients, or why a dish is good for them.
    """ 
    print("called dish_data", dish_name)
    cleaned_dish = get_dish_data(dish_name, restaurant_id)
    return json.dumps(cleaned_dish, default=str)

@tool
def dish_name_with_veg_nonveg_category(restaurant_id: str):
    """Get all the dish with it's veg and nonveg category"""
    print("called dish_name_with_veg_nonveg_category")
    menu = RestaurantMenuData.find_one({"_id": restaurant_id})
    list_dish = []
    menu = menu["menu"]
    for dish in menu:
        a1 = {}
        a1["dish_name"] = dish["dish_name"]
        a1["food_category"] = dish["food_category"]
        list_dish.append(a1)
    return json.dumps(list_dish, default=str)

@tool
def get_dish_counts(restaurant_id: str):
    """Get the count of vegetarian and non-vegetarian dishes."""
    print("called get_dish_counts")
    menu_data = RestaurantMenuData.find_one({"_id": restaurant_id})
    if not menu_data or "menu" not in menu_data:
        return json.dumps({"veg": 0, "non_veg": 0})
    
    menu = menu_data["menu"]
    veg_count = 0
    non_veg_count = 0
    
    for dish in menu:
        cat = dish.get("food_category", "").lower()
        if "Nonvegetarian" in cat and "Vegetarian" in cat: # Handle "non-veg", "non_veg"
            non_veg_count += 1
        elif "veg" in cat:
            veg_count += 1
            
    return json.dumps({"veg": veg_count, "non_veg": non_veg_count})

@tool
def get_dish_ingredients(dish_name: str, restaurant_id: str):
    """
    Get ONLY the ingredients of a specific dish.
    Use this when the user specifically asks "What are the ingredients in X?" or "What is in X?"
    Returns: JSON string with dish name and ingredients list.
    """
    print("called get_dish_ingredients", dish_name, restaurant_id)
    dish = get_dish_data(dish_name, restaurant_id)

    if not dish:
        return json.dumps({"error": f"Dish '{dish_name}' not found."})

    ingredients = dish.get("dish_variants", {}).get("normal", {}).get("full", {}).get("ingredients", [])

    return json.dumps({"dish_name": dish.get("dish_name", dish_name), "ingredients": ingredients}, default=str)

@tool
def get_menu_category_dish(restaurant_id: str):
    """Get the list of meal categories and dish name."""
    print("called get_menu_category_dish")
    menu = RestaurantMenuData.find_one({"_id": restaurant_id})
    if not menu_data or "menu" not in menu_data:
        return json.dumps({"error": "Menu data not found."})
    menu = menu["menu"]

    #making list in which there dish name as category and meal_category as value
    list_dish = []
    for dish in menu:
        a1 = {}
        a1["dish_name"] = dish["dish_name"]
        a1["meal_category"] = dish["meal_category"]
        list_dish.append(a1)
    return json.dumps(list_dish, default=str)

@tool
def get_list_of_meal_category(restaurant_id: str):
    """Get the list of menu meal's categories."""
    print("called get_list_of_meal_category")
    menu_data = RestaurantMenuData.find_one({"_id": restaurant_id})
    if not menu_data or "menu" not in menu_data:
        return json.dumps({"error": "Menu data not found."})
    
    menu = menu_data["menu"]
    meal_categories = set()
        
    for dish in menu:
        categories = dish.get("meal_category", [])
        if isinstance(categories, list):
            meal_categories.update(categories)
        else:
            meal_categories.add(categories)
        
    return json.dumps(list(meal_categories), default=str)
        
@tool
def get_list_of_dish_name_of_category(restaurant_id: str, meal_category: str):
    """Get the list of menu dish's names of a specific category."""
    print("called get_list_of_dish_name_of_category", meal_category)
    menu_data = RestaurantMenuData.find_one({"_id": restaurant_id})
    if not menu_data or "menu" not in menu_data:
        return json.dumps({"error": "Menu data not found."})
    
    menu = menu_data["menu"]
    dish_names = set()
    
    for dish in menu:
        if meal_category in dish.get("meal_category", []):
            dish_names.add(dish.get("dish_name", ""))
    
    return json.dumps(list(dish_names), default=str)
















