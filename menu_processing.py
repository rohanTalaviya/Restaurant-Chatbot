from connection_db import RestroData, RestaurantMenuData

def get_clean_dish_data(dishes):
    """
    Processes a list of dish dictionaries to retain only main information
    and reformat ingredients and nutrients.
    """
    keys_to_remove = [
        "lack_of_nutrients_data",
        "less_important_claims",
        "claims_details",
        "cooking_style",
        "created_at",
        "updated_at",
        "last_reminder_sent",
        "is_processing",
        "is_addon_apply",
        "is_image_updated",
        "not_found_ingredient",
        "verified",
        "is_Verified",
        "nutritional_values_of_dish",
        "is_edited",
        "special",
        "dish_img_url"
    ]

    for dish in dishes:
        # Remove top-level keys
        for key in keys_to_remove:
            if key in dish:
                del dish[key]

        # Process dish variants
        if "dish_variants" in dish:
            for variant_name, variant_data in dish["dish_variants"].items():
                for size_name, size_data in variant_data.items():
                    
                    # 1. Transform ingredients
                    if "ingredients" in size_data:
                        new_ingredients = {}
                        for ing in size_data["ingredients"]:
                            name = ing.get("name")
                            quantity = ing.get("quantity")
                            unit = ing.get("unit")
                            if name and quantity is not None:
                                new_ingredients[name] = f"{quantity}{unit}"
                        size_data["ingredients"] = new_ingredients

                    # 2. Remove nutrients
                    if "nutrients" in size_data:
                        del size_data["nutrients"]

                    # 3. Transform calculate_nutrients
                    if "calculate_nutrients" in size_data:
                        new_calc_nutrients = {}
                        for category, nutrients_list in size_data["calculate_nutrients"].items():
                            if isinstance(nutrients_list, list):
                                for nutrient in nutrients_list:
                                    name = nutrient.get("name")
                                    value = nutrient.get("value")
                                    unit = nutrient.get("unit")
                                    if name and value is not None:
                                        new_calc_nutrients[name] = f"{value}{unit}"
                        size_data["calculate_nutrients"] = new_calc_nutrients

    return dishes

def get_restaurant_data(restaurant_id):
    """Get restaurant data for a specific restaurant."""
    resto = RestroData.find_one({"_id": restaurant_id})
    restaurant = {}
    restaurant["name"] = resto["name"]
    restaurant["address"] = resto["address"]
    return restaurant

def get_clean_dish_data_one(dish):
    """
    Processes a list of dish dictionaries to retain only main information
    and reformat ingredients and nutrients.
    """
    keys_to_remove = [
        "lack_of_nutrients_data",
        "less_important_claims",
        "claims_details",
        "cooking_style",
        "created_at",
        "updated_at",
        "last_reminder_sent",
        "is_processing",
        "is_addon_apply",
        "is_image_updated",
        "not_found_ingredient",
        "verified",
        "is_Verified",
        "nutritional_values_of_dish",
        "is_edited",
        "special",
        "dish_img_url"
    ]

    # Remove top-level keys
    for key in keys_to_remove:
        if key in dish:
            del dish[key]

    # Process dish variants
    if "dish_variants" in dish:
        for variant_name, variant_data in dish["dish_variants"].items():
            for size_name, size_data in variant_data.items():
                
                # 1. Transform ingredients
                if "ingredients" in size_data:
                    new_ingredients = {}
                    for ing in size_data["ingredients"]:
                        name = ing.get("name")
                        quantity = ing.get("quantity")
                        unit = ing.get("unit")
                        if name and quantity is not None:
                            new_ingredients[name] = f"{quantity}{unit}"
                    size_data["ingredients"] = new_ingredients

                # 2. Remove nutrients
                if "nutrients" in size_data:
                    del size_data["nutrients"]

                # 3. Transform calculate_nutrients
                if "calculate_nutrients" in size_data:
                    new_calc_nutrients = {}
                    for category, nutrients_list in size_data["calculate_nutrients"].items():
                        if isinstance(nutrients_list, list):
                            for nutrient in nutrients_list:
                                name = nutrient.get("name")
                                value = nutrient.get("value")
                                unit = nutrient.get("unit")
                                if name and value is not None:
                                    new_calc_nutrients[name] = f"{value}{unit}"
                    size_data["calculate_nutrients"] = new_calc_nutrients

    return dish

def get_dish_data(dish_name: str, restaurant_id: str):

    menu = RestaurantMenuData.find_one({"_id": restaurant_id})
    menu = menu["menu"]
    dish_main = {}
    
    # Normalize target name: lower case, strip, replace non-breaking hyphen
    target_name = dish_name.lower().strip().replace("\u2011", "-")

    for dish in menu:
        # Normalize db name
        db_name = dish.get("dish_name", "").lower().strip().replace("\u2011", "-")
        
        if db_name == target_name:
            dish_main = dish
            break
            
    dish_data = get_clean_dish_data_one(dish_main)
    return dish_data