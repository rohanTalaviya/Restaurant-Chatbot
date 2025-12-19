from datetime import date, datetime, timedelta
import json
import logging
import math
import os
import random
from typing import Any, Dict, List, Optional, Set
import pytz
from connection_db import db
from langchain_core.tools import tool
import config


logger = logging.getLogger(__name__)

menu_collection = db["RestaurantMenuData"]
user_data_collection = db["UserData"]

def get_selected_meal(user_doc=None):
    """
    Return the current meal name based on the user's local time.
    Uses user_doc["tz_name"] if available, else defaults to Asia/Kolkata.
    """

    # Resolve tz_name from user doc
    tz_name = None
    if user_doc and isinstance(user_doc, dict):
        tz_name = user_doc.get("tz_name")

    if not tz_name:
        tz_name = "Asia/Kolkata"  # fallback

    try:
        now_local = datetime.now(pytz.timezone(tz_name))
    except Exception:
        now_local = datetime.now(pytz.timezone("Asia/Kolkata"))

    current_hour = now_local.hour

    meal_times = {
        "Breakfast": (3, 11),   # 3:00 AM - 10:59 AM
        "Lunch":     (11, 16),  # 11:00 AM - 3:59 PM
        "Snacks":    (16, 17),  # 4:00 PM - 4:59 PM
        "Dinner":    (17, 3),   # 5:00 PM - 2:59 AM (overnight)
    }

    selected_meal = "N/A"  # Default if no match
    for meal, (start, end) in meal_times.items():
        if start < end:  # Normal window (same day)
            if start <= current_hour < end:
                selected_meal = meal
                break
        else:  # Overnight case (crosses midnight)
            if current_hour >= start or current_hour < end:
                selected_meal = meal
                break

    return selected_meal

# Must match your app’s ranges
MEAL_RANGES = {
    "Breakfast": (3, 11),   # 03:00–10:59
    "Lunch":     (11, 16),  # 11:00–15:59
    "Snacks":    (16, 17),  # 16:00–16:59
    "Dinner":    (17, 3),   # 17:00–02:59 (overnight)
}

def meal_window_bounds(now_local: datetime, meal: str) -> tuple[datetime, datetime]:
    """
    Given a local 'now_local' and a meal name, return (start, end) datetimes
    for the *current* occurrence of that meal's window in the same timezone
    as 'now_local'. Handles overnight windows (e.g., 17 → 03 next day).
    """
    if meal not in MEAL_RANGES:
        # Unknown meal → return a degenerate window covering just now
        return now_local, now_local

    start_h, end_h = MEAL_RANGES[meal]
    # Normalize to the local tz and zero-out minutes/seconds for clean boundaries
    base = now_local.replace(minute=0, second=0, microsecond=0)

    if start_h < end_h:
        # Same-day window (e.g., 03 → 11)
        start = base.replace(hour=start_h)
        end   = base.replace(hour=end_h)
        # If it's early morning before 'start', we are actually in yesterday's window
        if now_local < start:
            start -= timedelta(days=1)
            end   -= timedelta(days=1)
    else:
        # Overnight window (e.g., 17 → 03 next day)
        # Default: today at start_h to tomorrow at end_h
        start = base.replace(hour=start_h)
        end   = (base + timedelta(days=1)).replace(hour=end_h)
        # If we're after midnight but before end_h, we're still in "yesterday's" dinner
        if now_local.hour < end_h:
            start -= timedelta(days=1)
            end   -= timedelta(days=1)

    return start, end


def fetch_user_data(user_id):
    User = user_data_collection.find_one({"_id": user_id})
    if not User:
        return None
    return User

def user_data_process(data):

    # Constants
    MACRO_CALORIES = {"carbs": 4, "protein": 4, "fats": 9}
    DAILY_FIBER = {"women": 25, "men": 38}
    TEMPERATURE_FACTORS = {
        "Cold (Below 10°C)": 1.2,
        "Moderately Cold (10°C to 18°C)": 1.07,
        "Neutral (18°C to 25°C)": 1.0,
        "Warm (25°C to 30°C)": 1.03,
        "Hot (Above 30°C)": 1.07,
        "Extremely Hot (Above 35°C)": 1.15,
    }
    ACTIVITY_FACTORS = {
        "Sedentary": 1.2,
        "Lightly active": 1.375,
        "Moderate": 1.55,
        "Very active": 1.725,
        "Super active": 1.9,
    }
    EXERCISE_FACTORS = {
        "No exercise": 0,
        "Light": 0.175,
        "Moderate": 0.35,
        "Heavy": 0.525,
        "Very heavy": 0.7,
    }
    # Map yoga types to exercise factors
    YOGA_FACTORS = {
        "None": 0,
        "Light": 0.175,
        "Moderate": 0.35,
        "Heavy": 0.525,
    }

    # Constants for Macronutrient Ratios
    MACRONUTRIENT_RATIOS = {
        "Standard": {
            "Muscle Gain": {
                "Male": {
                    "18-40": {"Carbs": (0.50, 0.55), "Proteins": (0.20, 0.25), "Fats": (0.20, 0.25), "Fiber": 38},
                    "40+": {"Carbs": (0.50, 0.55), "Proteins": (0.15, 0.20), "Fats": (0.25, 0.30), "Fiber": 38}
                },
                "Female": {
                    "18-40": {"Carbs": (0.50, 0.55), "Proteins": (0.20, 0.22), "Fats": (0.20, 0.25), "Fiber": 25},
                    "40+": {"Carbs": (0.50, 0.55), "Proteins": (0.15, 0.20), "Fats": (0.25, 0.30), "Fiber": 25}
                }
            },
            "Weight Loss": {
                "Male": {
                    "18-40": {"Carbs": (0.40, 0.45), "Proteins": (0.20, 0.25), "Fats": (0.30, 0.35), "Fiber": 38},
                    "40+": {"Carbs": (0.45, 0.50), "Proteins": (0.15, 0.20), "Fats": (0.30, 0.35), "Fiber": 38}
                },
                "Female": {
                    "18-40": {"Carbs": (0.40, 0.45), "Proteins": (0.20, 0.22), "Fats": (0.30, 0.35), "Fiber": 25},
                    "40+": {"Carbs": (0.45, 0.50), "Proteins": (0.15, 0.20), "Fats": (0.30, 0.35), "Fiber": 25}
                }
            },
            "Healthy Eating": {
                "Male": {
                    "18-40": {"Carbs": (0.55, 0.60), "Proteins": (0.10, 0.15), "Fats": (0.20, 0.25), "Fiber": 38},
                    "40+": {"Carbs": (0.50, 0.55), "Proteins": (0.10, 0.12), "Fats": (0.25, 0.30), "Fiber": 38}
                },
                "Female": {
                    "18-40": {"Carbs": (0.55, 0.60), "Proteins": (0.10, 0.15), "Fats": (0.20, 0.25), "Fiber": 25},
                    "40+": {"Carbs": (0.50, 0.55), "Proteins": (0.10, 0.12), "Fats": (0.25, 0.30), "Fiber": 25}
                }
            }
        },
        "Diabetic": {
            "Carbs": (0.45, 0.45),    # 45% fixed
            "Proteins": (0.2, 0.2),   # 20% fixed
            "Fats": (0.35, 0.35),     # 35% fixed
        }
    }

    # Fiber requirements (per meal based on gender)
    FIBER_REQUIREMENTS = {
        "Female": (6, 9),  # 6-9 grams per meal
        "Male": (10, 13),  # 10-13 grams per meal
        "Not prefer to say": (6, 13)
    }


    # Functions
    def calculate_bmr(weight, height, age, gender):
        if gender == "Male":
            return (10 * weight) + (6.25 * height) - (5 * age) + 5
        elif gender == "Female":
            return (10 * weight) + (6.25 * height) - (5 * age) - 161
        else:
            return (((10 * weight) + (6.25 * height) - (5 * age) + 5) + ((10 * weight) + (6.25 * height) - (5 * age) - 161)) / 2

    def calculate_tdee(bmr, temp_factor , activity_factor, exercise_factor, goal_factor):
        adjusted_bmr = bmr * temp_factor
        tdee1 = adjusted_bmr * activity_factor
        tdee2 = tdee1 + (exercise_factor * tdee1)
        tdee3 = tdee2 * goal_factor
        return tdee1, tdee2, tdee3

    # Function to distribute caloric intake across meals
    def calculate_meal_distribution(tdee3):
        # Meal percentage ranges
        meal_percentages = {
            "Breakfast": (0.2, 0.25),  # 20-25%
            "Lunch": (0.3, 0.35),     # 30-35%
            "Snacks": (0.1, 0.15),    # 10-15%
            "Dinner": (0.3, 0.35),    # 30-35%
        }

        # Calculate calorie ranges for each meal
        meal_calories = {}
        for meal, (low, high) in meal_percentages.items():
            low_calories = tdee3 * low
            high_calories = tdee3 * high
            meal_calories[meal] = (low_calories, high_calories)

        return meal_calories

    # Function to calculate fixed calories based on hunger index
    def calculate_fixed_calories(meal_distribution, hunger_level):
        fixed_calories = {}
        for meal, (low, high) in meal_distribution.items():
            if hunger_level == "Low":
                fixed_calories[meal] = low  # Lowest value
            elif hunger_level == "Normal":
                fixed_calories[meal] = (low + high) / 2  # Midpoint value
            elif hunger_level == "High":
                fixed_calories[meal] = high  # Highest value
        return fixed_calories

    def calculate_macronutrients(calories, profile_type, gender,goal_type,age_group):

        ratios = MACRONUTRIENT_RATIOS[profile_type][goal_type][gender][age_group]
        fiber_range = FIBER_REQUIREMENTS[gender]

        # Macronutrient calculations
        carb_kcal = (calories * ratios["Carbs"][0], calories * ratios["Carbs"][1])  # Carbs in kcal
        protein_kcal = (calories * ratios["Proteins"][0], calories * ratios["Proteins"][1])  # Protein in kcal
        fat_kcal = (calories * ratios["Fats"][0], calories * ratios["Fats"][1])  # Fats in kcal

        # Convert kcal to grams
        carbs_grams = [carb_kcal[0] / 4, carb_kcal[1] / 4]  # Carbs in grams
        proteins_grams = [protein_kcal[0] / 4, protein_kcal[1] / 4]  # Proteins in grams
        fats_grams = [fat_kcal[0] / 9, fat_kcal[1] / 9]  # Fats in grams
        fiber_grams = list(fiber_range)  # Convert fiber tuple to list
        carbs=(carbs_grams[1]+carbs_grams[0])/2
        protein=(protein_kcal[1]+protein_kcal[0])/2
        fats=()
        

        # Return formatted dictionary
        return {
            "Carbs (g)": carbs_grams,
            "Proteins (g)": proteins_grams,
            "Fats (g)": fats_grams,
            "Fiber (g)": fiber_grams,
        }


    def calculate_age(birth_date_str):
    # Parse the string to a date object
        birth_date = datetime.strptime(birth_date_str, "%d-%m-%Y").date()
        today = date.today()
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        return age

    # userinput
    country_code = data["mobile_number"]
    gender = data["gender"]
    weight = float(data.get("weight", {}).get("value", 0.0))
    height = float(data.get("height", {}).get("value", 0.0))
    weight_unit = data.get("weight", {}).get("unit", "kg")
    height_unit = data.get("height", {}).get("unit", "cm")
    dob = data["dob"]
    tz_name=data["tz_name"]
    age = calculate_age(dob)
    temperature = "Neutral (18°C to 25°C)"

    daily_routine = data["life_routine"]

    # Activity Selection
    activity_type = data["gym_or_yoga"]
    selected_activity = None  # Track the specific activity selected

    if activity_type == "Gym":
        selected_activity = data["intensity"]
        activity_factor = EXERCISE_FACTORS[selected_activity]
    elif activity_type == "Yoga":
        selected_activity = data["intensity"]
        activity_factor = YOGA_FACTORS[selected_activity]
    else:
        activity_factor = 0


    # Goal Consideration Inputs
    goal_subcategory = None  # To store sub-goal details

    goal = data["goal"]

    # Sub-options for Muscle Gain and Weight Loss
    if goal == "Muscle Gain":
        goal_subcategory = "Moderate Muscle Gain (Balanced Approach)"
        muscle_gain_factors = {
            "Lean Muscle Gain (Slow and Controlled)": 1.075,
            "Moderate Muscle Gain (Balanced Approach)": 1.175,
            "Aggressive Muscle Gain (Rapid Bulking)": 1.275,
        }
        goal_factor = muscle_gain_factors[goal_subcategory]

    elif goal == "Weight Loss":
        goal_subcategory = "Moderate Weight Loss (Balanced Approach)"
        fat_loss_factors = {
            "Mild Weight Loss (Slow and Sustainable)": 0.925,
            "Moderate Weight Loss (Balanced Approach)": 0.825,
            "Aggressive Weight Loss (Rapid Results)": 0.725,
        }
        goal_factor = fat_loss_factors[goal_subcategory]
    else:
        goal_factor = 1.0

    hunger_level = data["hunger_level"]

    # Calculate BMR
    bmr = calculate_bmr(weight, height, age, gender)


    # Adjusted TDEE
    tdee1, tdee2, tdee3 = calculate_tdee(
        bmr,
        TEMPERATURE_FACTORS[temperature],
        ACTIVITY_FACTORS[daily_routine],
        activity_factor,
        goal_factor
    )

    # Distribute calories for TDEE3
    meal_distribution = calculate_meal_distribution(tdee3)
    fixed_meal_calories = calculate_fixed_calories(meal_distribution, hunger_level)

    # Display the result for the selected meal category
    selected_meal = get_selected_meal(data)

    profile_type = "Standard"

    # Get calories for the selected meal category (from previous step)
    selected_meal_calories = fixed_meal_calories[selected_meal]  # Calories for selected meal

    if 18 <= age <= 40:
        age_group = "18-40"
    elif age > 40:
        age_group = "40+"
    else:
        age_group = "18-40"

    # Calculate macronutrients
    macronutrients = calculate_macronutrients(selected_meal_calories, profile_type, gender, goal,age_group)

    # Display results
    # User Data JSON
    user_input = {
        "Country Code": country_code,
        "tz_name":tz_name,
        "Gender": gender,
        "Weight (kg)": weight,
        "Height (cm)": height,
        "Age": age,
        "Temperature": temperature,
        "daily_routine": daily_routine,
        "Activity Type": activity_type,
        "Activity Sub-Category": selected_activity if activity_type != "None" else "None",
        "Goal": goal,
        "Goal Sub-Category": goal_subcategory if goal_subcategory else "None",
        "Hunger Level": hunger_level,
        "Selected Meal": selected_meal,
        "BMR (kcal/day)": bmr,
        "TDEE (kcal/day)": {"Activity Level": tdee1, "Exercise/Yoga Adjusted": tdee2, "Goal Adjusted": tdee3},
        "Hunger Level": hunger_level,
        "Fixed Calories": fixed_meal_calories[selected_meal],
        "profile_type": profile_type
    }
    return user_input
 
def calculate_nutrient_percentages(user_input):
        tdee = user_input["Fixed Calories"]
        daily_tdee = user_input["TDEE (kcal/day)"].get("Goal Adjusted")
        age = user_input["Age"]
        gender = user_input["Gender"]
        goal = user_input["Goal"]
        weight = user_input["Weight (kg)"]  

        logger.debug(f"let's calculating nutrient percentage : age = {age}, gender = {gender}, goal = {goal}, weight = {weight}")

        protein_min = protein_max = carbs_min = carbs_max = fats_min = fats_max = fiber = 0

        # Clamp the age within the supported range
        if age < 18:
            age = 18
        elif age > 60:
            age = 60

        # Initialize boundaries and nutrient percentages for each group
        if goal == "Muscle Gain":
            if gender == "Male":
                if 18 <= age <= 40:
                    protein_min, protein_max = 20, 25
                    carbs_min, carbs_max = 50, 55
                    fats_min, fats_max = 20, 25
                    fiber_min,fiber_max = 10,13
                    prot_g_low, prot_g_high = 1.6, 2.2
                    daily_fiber = 38
                elif 40 < age <= 60:
                    protein_min, protein_max = 15, 20
                    carbs_min, carbs_max = 50, 55
                    fats_min, fats_max = 25, 30
                    prot_g_low, prot_g_high = 1.2, 1.5
                    fiber_min,fiber_max = 6,9
                    daily_fiber = 25
            elif gender == "Female":
                if 18 <= age <= 40:
                    protein_min, protein_max = 20, 22
                    carbs_min, carbs_max = 50, 55
                    fats_min, fats_max = 20, 25
                    fiber_min,fiber_max = 6,9
                    prot_g_low, prot_g_high = 1.4, 1.8
                    daily_fiber = 25
                elif 40 < age <= 60:
                    protein_min, protein_max = 15, 20
                    carbs_min, carbs_max = 50, 55
                    fats_min, fats_max = 25, 30
                    fiber_min,fiber_max = 6,9
                    prot_g_low, prot_g_high = 1.2, 1.5
                    daily_fiber = 25

        elif goal == "Weight Loss":
            if gender == "Male":
                if 18 <= age <= 40:
                    protein_min, protein_max = 20, 25
                    carbs_min, carbs_max = 40, 45
                    fats_min, fats_max = 30, 35
                    fiber_min,fiber_max = 10,13
                    prot_g_low, prot_g_high = 1.8, 2.2
                    daily_fiber = 38
                elif 40 < age <= 60:
                    protein_min, protein_max = 18, 20
                    carbs_min, carbs_max = 45, 50
                    fats_min, fats_max = 30, 35
                    fiber_min,fiber_max = 6,9
                    prot_g_low, prot_g_high = 1.4, 1.8
                    daily_fiber = 25
            elif gender == "Female":
                if 18 <= age <= 40:
                    protein_min, protein_max = 18, 22
                    carbs_min, carbs_max = 40, 45
                    fats_min, fats_max = 30, 35
                    fiber_min,fiber_max = 6,9
                    prot_g_low, prot_g_high = 1.6, 2.0
                    daily_fiber = 25
                elif 40 < age <= 60:
                    protein_min, protein_max = 18, 20
                    carbs_min, carbs_max = 45, 50
                    fats_min, fats_max = 30, 35
                    fiber_min,fiber_max = 6,9
                    prot_g_low, prot_g_high = 1.4, 1.8
                    daily_fiber = 25

        elif goal == "Healthy Eating":
            if gender == "Male":
                if 18 <= age <= 40:
                    protein_min, protein_max = 10, 15
                    carbs_min, carbs_max = 55, 60
                    fats_min, fats_max = 20, 25
                    fiber_min,fiber_max = 10,13
                    prot_g_low, prot_g_high = 0.8, 1.2
                    daily_fiber = 38
                elif 40 < age <= 60:
                    protein_min, protein_max = 10, 12
                    carbs_min, carbs_max = 50, 55
                    fats_min, fats_max = 25, 30
                    fiber_min,fiber_max = 6,9
                    prot_g_low, prot_g_high = 0.8, 1.0
                    daily_fiber = 25
            elif gender == "Female":
                if 18 <= age <= 40:
                    protein_min, protein_max = 10, 12
                    carbs_min, carbs_max = 55, 60
                    fats_min, fats_max = 20, 25
                    fiber_min,fiber_max = 6,9
                    prot_g_low, prot_g_high = 0.8, 1.0
                    daily_fiber = 25
                elif 40 < age <= 60:
                    protein_min, protein_max = 10, 12
                    carbs_min, carbs_max = 50, 55
                    fats_min, fats_max = 25, 30
                    fiber_min,fiber_max = 6,9
                    prot_g_low, prot_g_high = 0.8, 1.0
                    daily_fiber = 25

        # Calculate nutrient percentages using linear interpolation
        age_range = (18, 40) if age <= 40 else (40, 60)
        protein_factor = (protein_max - protein_min) / (age_range[1] - age_range[0])
        protein_g_factor = (prot_g_high - prot_g_low) / (age_range[1] - age_range[0])
        carbs_factor = (carbs_max - carbs_min) / (age_range[1] - age_range[0])
        fats_factor = (fats_max - fats_min) / (age_range[1] - age_range[0])
        fiber_factor = (fiber_max - fiber_min) / (age_range[1] - age_range[0])

        protein_percentage = protein_min + ((age - age_range[0]) * protein_factor)
        protein_g = prot_g_low + ((age - age_range[0]) * protein_g_factor)
        carbs_percentage = carbs_max - ((age - age_range[0]) * carbs_factor)  # Invert for decrease
        fats_percentage = fats_min + ((age - age_range[0]) * fats_factor)
        fiber_grams = max(fiber_min, min(fiber_max, fiber_min + ((age - age_range[0]) * fiber_factor)))
        fiber_kcal = fiber_grams*2
        tdee1 = tdee - fiber_kcal

        # Calculate actual nutrient values
        proteink = (protein_percentage * tdee1) / 100
        protein_g1 = (weight * protein_g)*4
        
        # Define meal percentage ranges (as tuples with low, high)
        meal_percentages = {
            "Breakfast": (0.20, 0.25),  # 20-25%
            "Lunch": (0.30, 0.35),      # 30-35%
            "Snacks": (0.10, 0.15),     # 10-15%
            "Dinner": (0.30, 0.35),     # 30-35%
        }

        # Get the selected meal (you can use your get_selected_meal function)
        selected_meal = get_selected_meal(user_input)

        # Adjust meal percentage based on hunger level
        hunger_level = user_input.get("Hunger Level", "Normal")  # Default to "Normal" if no hunger level provided

        if hunger_level == "High":
            meal_percentage = meal_percentages[selected_meal][1]  # Use max value for high hunger
        elif hunger_level == "Low":
            meal_percentage = meal_percentages[selected_meal][0]  # Use min value for low hunger
        else:
            # Normal hunger, use average of the range
            low, high = meal_percentages[selected_meal]
            meal_percentage = (low + high) / 2

        # Calculate the protein for the selected meal based on hunger level
        protein_per_meal = protein_g1 * meal_percentage
        
        protein = max(proteink, protein_per_meal)  # Ensure we are getting the correct protein value
        fats = (tdee1 * fats_percentage) / 100
        carbs = tdee1 - fats - protein
        # Now, you can return the correct fiber in grams and as a percentage
        p = (protein / 4)  # Protein in grams (1g protein = 4 calories)
        f = (fats / 9)  # Fats in grams (1g fat = 9 calories)
        c = (carbs / 4)  # Carbs in grams (1g carbs = 4 calories)

        daily_fiber_g = daily_fiber
        daily_fiber_kcal = daily_fiber_g * 2
        
        daily_tdee1 = daily_tdee - daily_fiber_kcal
        
        daily_protein_cal = (protein_percentage * daily_tdee1) / 100
        daily_carbs_cal   = (carbs_percentage   * daily_tdee1) / 100
        daily_fats_cal    = (fats_percentage    * daily_tdee1) / 100

        # Convert calories to grams:
        daily_protein_g = daily_protein_cal / 4    # 4 kcal per g protein
        daily_carbs_g   = daily_carbs_cal   / 4    # 4 kcal per g carbs
        daily_fats_g    = daily_fats_cal    / 9    # 9 kcal per g fat

        # Total daily kcals = tdee (or tdee1 if subtracting fiber kcals)
        daily_calories = daily_tdee

        return {
            "Protein (%)": round(((protein * 100) / tdee), 2),
            "Carbohydrates (%)": round(((carbs * 100) / tdee), 2),
            "Fats (%)": round(((fats * 100) / tdee), 2),
            "Fiber (g/day)": round(fiber_grams, 2),  # Return fiber in grams
            "p": round(p, 2),  # Protein in grams
            "c": round(c, 2),  # Carbs in grams
            "fa": round(f, 2),  # Fats in grams
            "fiber": round(fiber_grams, 2),  # Fiber in grams (to avoid confusion with percentage)
            "tdee": round(tdee, 2),  # Total Daily Energy Expenditure (TDEE)
            "daily_kcal": round(daily_tdee,2),
            "daily_protein_g": round(daily_protein_g,2),
            "daily_carbs_g": round(daily_carbs_g,2),
            "daily_fats_g": round(daily_fats_g,2),
            "daily_fiber_g": round(daily_fiber_g,2)
            
        }

@tool
def recommend_dishes(
    restro_id: str = None,
    user_id: str = None,
    is_group: str = "false",
) -> str:
    """
    Personalized Dish Recommendation Engine.
    
    Use this tool when the user asks:
    - "What should I eat?"
    - "Suggest something for me."
    - "What is good here?"
    
    This function analyzes the user's profile (goals, diet, allergies) and the current context (time of day, weather) to intelligently rank and recommend the best dishes from the menu.
    
    Args:
        restro_id (str, optional): The restaurant ID. Defaults to config if not provided.
        user_id (str, optional): The user ID. Defaults to config if not provided.
        is_group (str, optional): Set to "true" if recommending for a group. Defaults to "false".
        
    Returns:
        str: A JSON string containing a list of recommended dishes categorized by 'match' quality (e.g., "Best Match", "Good Match").
    """

    print("called recommend_dishes")
    # Use defaults from config if not provided
    if not restro_id:
        restro_id = config.RESTAURANT_ID
    if not user_id:
        user_id = config.USER_ID

    # Convert is_group to boolean
    is_group_bool = str(is_group).lower() == "true"

    # ---------------------------------------------------------------------
    # Base Weights / Constants
    # ---------------------------------------------------------------------
    default_factors = {
        "protein": 5,
        "carbs": 3,
        "fats": 2,
        "fibers": 1,
        "energy": 8,
        "density_factor": 2,
        "satiety_factor": 1,
        "euclidean_factor": 4,
        "timing_factor": 6,
        "rules_factor": 1,
    }
    rule_factors = {
        "protein_overrule_factor": 1,
        "low_carbs_overrule_factor": 1,
        "low_fat_overrule_factor": 1,
        "sugar_content_factor": 1,
        "sodium_content_factor": 1,
        "saturated_fat_factor": 1,
        "cholesterol_factor": 1,
        "caloric_density_factor": 1,
        "good_fats_factor": 1,
    }

    PENALTY_RULE_KEYS = {
        "sugar_content_factor",
        "sodium_content_factor",
        "saturated_fat_factor",
        "cholesterol_factor",
        "caloric_density_factor",
    }
    
    # Birthday multipliers
    BIRTHDAY_DEFAULT_MULTS = {
        "protein": 0.90,
        "carbs": 1.10,
        "fats": 1.10,
        "fibers": 0.90,
        "energy": 1.10,
        "density_factor": 1.05,
        "satiety_factor": 0.90,
        "euclidean_factor": 0.90,
        "timing_factor": 10.00,
        "rules_factor": 0.90
    }
    BIRTHDAY_RULE_SCALE = 0.85

    # Activity Multipliers
    ACTIVITY_MULTS = {
        "sedentary": {
            "default": {
                "protein": 1.00, "carbs": 0.85, "fats": 1.05, "fibers": 1.05,
                "energy": 0.90, "density_factor": 1.05, "satiety_factor": 1.00, "euclidean_factor": 1.00, "timing_factor": 1.00, "rules_factor": 1.00
            },
            "rule_overrides": {"sugar_content_factor": 1.10}
        },
        "light": {
            "default": {
                "protein": 1.05, "carbs": 0.95, "fats": 1.00, "fibers": 1.05,
                "energy": 0.95, "density_factor": 1.00, "satiety_factor": 1.05, "euclidean_factor": 1.00, "timing_factor": 1.00, "rules_factor": 1.00
            },
            "rule_overrides": {"sugar_content_factor": 1.05}
        },
        "moderate": {
            "default": {
                "protein": 1.00, "carbs": 1.00, "fats": 1.00, "fibers": 1.00,
                "energy": 1.00, "density_factor": 1.00, "satiety_factor": 1.00, "euclidean_factor": 1.00, "timing_factor": 1.00, "rules_factor": 1.00
            },
            "rule_overrides": {}
        },
        "heavy": {
            "default": {
                "protein": 1.15, "carbs": 1.05, "fats": 0.95, "fibers": 1.00,
                "energy": 1.05, "density_factor": 1.00, "satiety_factor": 1.10, "euclidean_factor": 1.00, "timing_factor": 1.00, "rules_factor": 1.00
            },
            "rule_overrides": {"saturated_fat_factor": 0.95}
        },
        "very_heavy": {
            "default": {
                "protein": 1.25, "carbs": 1.15, "fats": 0.90, "fibers": 1.00,
                "energy": 1.10, "density_factor": 0.95, "satiety_factor": 1.15, "euclidean_factor": 1.00, "timing_factor": 1.00, "rules_factor": 1.00
            },
            "rule_overrides": {
                "saturated_fat_factor": 0.90, "sugar_content_factor": 0.95, "caloric_density_factor": 0.95
            }
        },
    }

    # ---------------------------------------------------------------------
    # Helper Functions (nested)
    # ---------------------------------------------------------------------
    def gaussian_fit(x: float, target: float, sigma_frac: float = 0.10) -> float:
        """Smooth closeness in (0..1]; higher is better."""
        eps = 1e-6
        if target <= 0:
            return 0.0
        sigma = max(abs(target) * sigma_frac, eps)
        z = (x - target) / sigma
        return float(math.exp(-(z * z)))

    def within_pct(x: float, target: float, pct: float) -> bool:
        """Is x within ±pct of target? pct as fraction (e.g., 0.18 = 18%)."""
        if target <= 0:
            return True
        lo, hi = target * (1 - pct), target * (1 + pct)
        return lo <= x <= hi

    def get_macro_value(macro_list: List[Dict[str, Any]], key: str) -> float:
        for item in macro_list or []:
            if item.get("name") == key:
                return float(item.get("value", 0.0) or 0.0)
        return 0.0

    def safe_percentage(value: Any) -> float:
        try:
            return float(str(value).replace("%", "").strip())
        except Exception:
            return 0.0

    def get_nutrients_data(dish: Dict[str, Any]) -> Dict[str, float]:
        nutrients_data = (
            dish.get("dish_variants", {})
            .get("normal", {})
            .get("full", {})
            .get("nutrients", [])
        )
        nutrients_to_extract = [
            "ENERC",
            "PROTCNT",
            "CHOAVLDF",
            "FATCE",
            "FIBTG",
            "FASAT",
            "TCHO",
            "CHOLC",
            "NA",
            "TOTALFREESUGARS",
            "FAPU",
            "FAMU",
        ]
        out = {}
        for n in nutrients_to_extract:
            out[n] = next(
                (float(i.get("quantity", 0.0) or 0.0) for i in nutrients_data if i.get("name") == n),
                0.0,
            )
        return out

    def get_user_activity_level(db, user_id: str) -> tuple[Optional[str], str]:
        doc = db["UserData"].find_one({"_id": user_id}, {"life_routine": 1}) or {}
        raw = doc.get("life_routine")

        def _normalize_activity(value) -> str:
            if value is None:
                return "sedentary"
            s = str(value).strip().lower().replace("-", "_").replace(" ", "_")
            num_map = {"1": "sedentary", "2": "light", "3": "heavy", "4": "very_heavy"}
            if s in num_map:
                return num_map[s]
            aliases = {
                # sedentary
                "sedentary": "sedentary",
                "very_low": "sedentary",
                "low": "sedentary",
                "deskjob": "sedentary",
                "inactive": "sedentary",

                # light
                "light": "light",
                "lightly_active": "light",
                "mild": "light",
                "casual": "light",
                "walking": "light",

                # moderate
                "moderate": "moderate",
                "medium": "moderate",
                "average": "moderate",
                "balanced": "moderate",
                "normal": "moderate",

                # heavy
                "heavy": "heavy",
                "high": "heavy",
                "active": "heavy",
                "post_workout": "heavy",
                "training": "heavy",
                "workout": "heavy",

                # very heavy (your “very active” bucket)
                "very_heavy": "very_heavy",
                "veryactive": "very_heavy",   # no underscore variant
                "very_active": "very_heavy",  # <-- this fixes your case
                "super_active": "very_heavy",
                "extremely_active": "very_heavy",
                "athlete": "very_heavy",
                "intense": "very_heavy",
            }
            return aliases.get(s, "moderate")

        normalized = _normalize_activity(raw)
        return raw, normalized

    def _parse_birthdate(s: Any) -> Optional[date]:
        s = str(s).strip() if s is not None else ""
        if not s:
            return None
        fmts = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y")
        for f in fmts:
            try:
                return datetime.strptime(s, f).date()
            except Exception:
                pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except Exception:
            return None

    def is_user_birthday(user_doc: Dict[str, Any], today: Optional[date] = None) -> bool:
        """Reads DOB from UserData and returns True if current date (month/day) matches."""
        if not isinstance(user_doc, dict):
            return False
        today = today or date.today()
        candidates = [
            user_doc.get("dob"),
            user_doc.get("date_of_birth"),
            user_doc.get("birthdate"),
            user_doc.get("birth_date"),
            (user_doc.get("profile") or {}).get("dob") if isinstance(user_doc.get("profile"), dict) else None,
        ]
        for c in candidates:
            bd = _parse_birthdate(c)
            if bd and bd.month == today.month and bd.day == today.day:
                return True
        return False

    def get_live_goal_value(
        dct: Dict[str, Any], key_main: str, key_alt: Optional[str] = None, default: float = 22.0
    ) -> float:
        if not isinstance(dct, dict):
            return default
        if key_main in dct and isinstance(dct[key_main], dict):
            return float(dct[key_main].get("value", default) or 0.0)
        if key_alt and key_alt in dct and isinstance(dct[key_alt], dict):
            return float(dct[key_alt].get("value", default) or 0.0)
        return default

    # ----------------- Rule Functions -----------------
    def protein_overrule(dist: Dict[str, Any]) -> int:
        try:
            protein = float(str(dist.get("proteins", "0")).replace("%", ""))
            carbs = float(str(dist.get("carbs", "0")).replace("%", ""))
            fats = float(str(dist.get("fats", "0")).replace("%", ""))

            if 8 <= protein <= 43:
                protein_score = 100
            else:
                protein_distance = min(abs(protein - 8), abs(protein - 43))
                protein_score = max(0, 100 - protein_distance * 2)

            carbs_penalty = (max(0.0, carbs - 65)) * 1.5
            fats_penalty = (max(0.0, fats - 30)) * 1.5

            score = (protein_score * 0.5) - (carbs_penalty * 0.25) - (fats_penalty * 0.25)
            return int(max(0, min(100, round(score))))
        except Exception:
            return 0

    def low_carbs_overrule(dist: Dict[str, Any]) -> int:
        try:
            protein = float(str(dist.get("proteins", "0")).replace("%", ""))
            carbs = float(str(dist.get("carbs", "0")).replace("%", ""))
            fats = float(str(dist.get("fats", "0")).replace("%", ""))

            if 45 <= carbs <= 60:
                carbs_score = 100
            else:
                carbs_distance = min(abs(carbs - 45), abs(carbs - 60))
                carbs_score = max(0, 100 - carbs_distance * 2)

            if 8 <= protein <= 43:
                protein_score = 100
            elif 3 <= protein <= 8 or 44 <= protein <= 58:
                protein_score = 80
            else:
                if protein < 3:
                    protein_distance = abs(protein - 3)
                elif 8 < protein < 44:
                    protein_distance = min(abs(protein - 8), abs(protein - 44))
                else:
                    protein_distance = abs(protein - 58)
                protein_score = max(0, 80 - protein_distance * 2)

            fats_penalty = 0
            if 8 <= protein <= 43 and fats > 35:
                fats_penalty = (fats - 35) * 1.5
            elif (3 <= protein <= 8 or 44 <= protein <= 58) and fats > 10:
                fats_penalty = (fats - 10) * 2
            elif protein < 3 or protein > 58:
                if fats > 35:
                    fats_penalty = (fats - 35) * 1.5

            score = (carbs_score * 0.4) + (protein_score * 0.4) - (fats_penalty * 0.2)
            return int(max(0, min(100, round(score))))
        except Exception:
            return 0

    def low_fat_overrule(dist: Dict[str, Any]) -> int:
        try:
            protein = float(str(dist.get("proteins", "0")).replace("%", ""))
            carbs = float(str(dist.get("carbs", "0")).replace("%", ""))
            fats = float(str(dist.get("fats", "0")).replace("%", ""))

            if 15 <= fats <= 30:
                fats_score = 100
            else:
                fats_distance = min(abs(fats - 15), abs(fats - 30))
                fats_score = max(0, 100 - fats_distance * 2)

            if 8 <= protein <= 43:
                protein_score = 100
            elif 3 <= protein <= 8 or 44 <= protein <= 58:
                protein_score = 80
            else:
                if protein < 3:
                    protein_distance = abs(protein - 3)
                elif 8 < protein < 44:
                    protein_distance = min(abs(protein - 8), abs(protein - 44))
                else:
                    protein_distance = abs(protein - 58)
                protein_score = max(0, 80 - protein_distance * 2)

            carbs_penalty = 0
            if 8 <= protein <= 43 and carbs > 65:
                carbs_penalty = (carbs - 65) * 1.5
            elif (3 <= protein <= 8 or 44 <= protein <= 58) and carbs > 60:
                carbs_penalty = (carbs - 60) * 2
            else:
                if carbs > 65:
                    carbs_penalty = (carbs - 65) * 1.5

            score = (fats_score * 0.4) + (protein_score * 0.4) - (carbs_penalty * 0.2)
            return int(max(0, min(100, round(score))))
        except Exception:
            return 0

    def sugar_content_rule(sugar_pct: float) -> int:
        try:
            sugar = float(sugar_pct)
            if sugar <= 10:
                score = 100
            elif sugar <= 20:
                score = max(0, 100 - (sugar - 10) * 2)
            elif sugar <= 30:
                score = max(0, 80 - (sugar - 20) * 3)
            else:
                score = max(0, 50 - (sugar - 30) * 4)
            return int(round(score))
        except Exception:
            return 0

    def sodium_content_rule(sodium: float, serving_size: float) -> int:
        try:
            sodium_per_100g = (float(sodium) * 100) / float(serving_size) if serving_size > 0 else 0.0
            if sodium_per_100g <= 400:
                score = 100
            elif sodium_per_100g <= 800:
                score = max(0, 100 - (sodium_per_100g - 400) * 0.05)
            elif sodium_per_100g <= 1200:
                score = max(0, 80 - (sodium_per_100g - 800) * 0.075)
            else:
                score = max(0, 50 - (sodium_per_100g - 1200) * 0.1)
            return int(round(score))
        except Exception:
            return 0

    def saturated_fat_rule(saturated_fat: float, serving_size: float) -> int:
        try:
            sat_per_100g = (float(saturated_fat) * 100) / float(serving_size) if serving_size > 0 else 0.0
            if sat_per_100g <= 2000:
                score = 100
            elif sat_per_100g <= 5000:
                score = max(0, 100 - (sat_per_100g - 2000) * 0.033)
            elif sat_per_100g <= 7000:
                score = max(0, 80 - (sat_per_100g - 5000) * 0.05)
            else:
                score = max(0, 50 - (sat_per_100g - 7000) * 0.067)
            return int(round(score))
        except Exception:
            return 0

    def cholesterol_rule(cholesterol: float, serving_size: float) -> int:
        try:
            chol_per_100g = (float(cholesterol) * 100) / float(serving_size) if serving_size > 0 else 0.0
            if chol_per_100g <= 75:
                score = 100
            elif chol_per_100g <= 150:
                score = max(0, 100 - (chol_per_100g - 75) * 0.266)
            elif chol_per_100g <= 200:
                score = max(0, 80 - (chol_per_100g - 150) * 0.4)
            else:
                score = max(0, 60 - (chol_per_100g - 200) * 0.5)
            return int(round(score))
        except Exception:
            return 0

    def caloric_density_rule(energy: float, serving_size: float) -> int:
        try:
            cd = (float(energy) * 100) / float(serving_size) if serving_size > 0 else 0.0
            if cd <= 200:
                score = 100
            elif cd <= 300:
                score = max(0, 100 - (cd - 200) * 0.2)
            elif cd <= 400:
                score = max(0, 80 - (cd - 300) * 0.3)
            else:
                score = max(0, 50 - (cd - 400) * 0.4)
            return int(round(score))
        except Exception:
            return 0

    def good_fats_rule(sat: float, poly: float, mono: float, serving_size: float) -> int:
        try:
            good = float(poly) + float(mono)
            good_per_100g = (good * 100) / float(serving_size) if serving_size > 0 else 0.0
            if good_per_100g <= 500:
                score = max(0, 50 + (good_per_100g / 500) * 30)
            elif good_per_100g <= 2000:
                score = max(80, 80 + ((good_per_100g - 500) / 1500) * 10)
            else:
                score = min(100, 90 + ((good_per_100g - 2000) / 1000) * 5)
            return int(round(score))
        except Exception:
            return 0

    # ---------- Timing helpers ----------
    def timing_categories_for_hour(hour: int) -> Set[str]:
        if 5 <= hour <= 10:
            return {"breakfast", "brunch", "snack"}
        elif 11 <= hour <= 15 or 19 <= hour <= 22:
            return {"lunch", "brunch", "snack", "dinner"}
        elif 16 <= hour <= 18:
            return {"snack"}
        else:
            return {"midnight snack", "snack"}

    def _norm_cat_set(vals) -> Set[str]:
        return {str(v).strip().lower() for v in (vals or []) if str(v).strip()}

    _TIMING_WEIGHTS = {
        "breakfast": 1.0,
        "lunch": 1.0,
        "dinner": 1.0,
        "brunch": 0.8,
        "snack": 0.6,
        "midnight snack": 0.7,
    }

    def timing_alignment_score(dish_cats: Set[str], hour: int) -> float:
        allowed = timing_categories_for_hour(hour)
        if not dish_cats:
            return 60.0 if "snack" in allowed else 50.0
        dish_cats = {c for c in dish_cats if c in _TIMING_WEIGHTS}
        if not dish_cats:
            return 50.0
        inter = dish_cats & allowed
        if not inter:
            return 35.0
        num = sum(_TIMING_WEIGHTS[c] for c in inter)
        den = sum(_TIMING_WEIGHTS[c] for c in dish_cats)
        return float(100.0 * num / (den or 1.0))

    # ----------------- Context Adjuster -----------------
    def adjust_factors(
        default_factors_in: Dict[str, float],
        rule_factors_in: Dict[str, float],
        *,
        hour: int,
        weekday: int,
        cuisine: Optional[str] = None,
        is_group: Optional[bool] = None,
        apply_context_layers: bool = True,
        special_occasion: bool = False,
        activity_level: Optional[str] = None,
    ):
        def _apply_multipliers(base: dict, mults: dict) -> dict:
            out = base.copy()
            for k, m in mults.items():
                if k in out:
                    out[k] = out[k] * m
            return out

        df = default_factors_in.copy()
        rf = rule_factors_in.copy()

        if apply_context_layers:

            # Meal timing
            if 5 <= hour <= 10:
                meal_default = {
                    "protein": 1.00, "carbs": 1.15, "fats": 0.90, "fibers": 1.05, "energy": 1.05,
                    "density_factor": 0.95, "satiety_factor": 0.95, "euclidean_factor": 1.00, "timing_factor": 2.00, "rules_factor": 1.00
                }
                meal_rule = {
                    "protein_overrule_factor": 1.00, "low_carbs_overrule_factor": 1.00, "low_fat_overrule_factor": 1.00,
                    "sugar_content_factor": 1.10, "sodium_content_factor": 1.00, "saturated_fat_factor": 1.00,
                    "cholesterol_factor": 1.00, "caloric_density_factor": 1.00, "good_fats_factor": 1.00
                }
            elif 11 <= hour <= 15:
                meal_default = {k: 1.00 for k in df.keys()}
                meal_rule = {k: 1.00 for k in rf.keys()}
            elif 18 <= hour <= 22:
                meal_default = {
                    "protein": 1.05, "carbs": 0.85, "fats": 1.10, "fibers": 1.00, "energy": 0.90,
                    "density_factor": 1.05, "satiety_factor": 1.10, "euclidean_factor": 1.00, "timing_factor": 2.00, "rules_factor": 1.00
                }
                meal_rule = {
                    "protein_overrule_factor": 1.00, "low_carbs_overrule_factor": 1.00, "low_fat_overrule_factor": 1.00,
                    "sugar_content_factor": 0.90, "sodium_content_factor": 1.00, "saturated_fat_factor": 1.00,
                    "cholesterol_factor": 1.00, "caloric_density_factor": 1.00, "good_fats_factor": 1.00
                }
            else:
                meal_default = {k: 1.00 for k in df.keys()}
                meal_rule = {k: 1.00 for k in rf.keys()}

            df = _apply_multipliers(df, meal_default)
            rf = _apply_multipliers(rf, meal_rule)

            # Day (weekend vs weekday)
            is_weekend = weekday >= 5
            if is_weekend:
                day_default = {
                    "protein": 0.95, "carbs": 1.05, "fats": 1.05, "fibers": 0.95, "energy": 1.05,
                    "density_factor": 1.05, "satiety_factor": 0.95, "euclidean_factor": 1.00, "timing_factor": 2.00, "rules_factor": 1.00
                }
                day_rule = {
                    "protein_overrule_factor": 1.00, "low_carbs_overrule_factor": 1.00, "low_fat_overrule_factor": 1.00,
                    "sugar_content_factor": 0.90, "sodium_content_factor": 1.00, "saturated_fat_factor": 1.00,
                    "cholesterol_factor": 1.00, "caloric_density_factor": 1.00, "good_fats_factor": 1.00
                }
            else:
                day_default = {k: 1.00 for k in df.keys()}
                day_rule = {k: 1.00 for k in rf.keys()}
            df = _apply_multipliers(df, day_default)
            rf = _apply_multipliers(rf, day_rule)

        # Birthday-only adjustment
        if special_occasion:
            df = {k: (df.get(k, 1.0) * BIRTHDAY_DEFAULT_MULTS.get(k, 1.0)) for k in df.keys()}
            for k in PENALTY_RULE_KEYS:
                if k in rf:
                    rf[k] = rf[k] * BIRTHDAY_RULE_SCALE

        # Cuisine tweak (per-dish)
        if isinstance(cuisine, str) and cuisine.strip():
            is_indian = cuisine.strip().lower() == "indian"
            if is_indian:
                cuisine_df_mult = {
                    "protein": 1.05, "carbs": 0.95, "fats": 1.05, "fibers": 1.05,
                    "energy": 1.00, "density_factor": 1.05, "satiety_factor": 1.05, "euclidean_factor": 1.00, "timing_factor": 2.00, "rules_factor": 1.00
                }
                rf["sugar_content_factor"] = rf.get("sugar_content_factor", 1.0) * 1.10
            else:
                cuisine_df_mult = {
                    "protein": 1.10, "carbs": 0.95, "fats": 1.00, "fibers": 1.05,
                    "energy": 0.95, "density_factor": 1.00, "satiety_factor": 1.00, "euclidean_factor": 1.00, "timing_factor": 2.00, "rules_factor": 1.00
                }
                rf["sodium_content_factor"] = rf.get("sodium_content_factor", 1.0) * 1.05
            df = _apply_multipliers(df, cuisine_df_mult)

        # Group vs Solo
        if is_group is not None:
            if is_group is True:
                dm_df_mult = {
                    "protein": 0.95, "carbs": 1.05, "fats": 1.05, "fibers": 0.95,
                    "energy": 1.05, "density_factor": 1.05, "satiety_factor": 0.95, "euclidean_factor": 0.95, "timing_factor": 2.00, "rules_factor": 1.00
                }
                penalty_scale = 0.90
            else:
                dm_df_mult = {
                    "protein": 1.05, "carbs": 0.95, "fats": 0.95, "fibers": 1.05,
                    "energy": 0.95, "density_factor": 0.95, "satiety_factor": 1.05, "euclidean_factor": 1.05, "timing_factor": 2.00, "rules_factor": 1.00
                }
                penalty_scale = 1.05
            df = _apply_multipliers(df, dm_df_mult)
            if penalty_scale != 1.0:
                for k in PENALTY_RULE_KEYS:
                    if k in rf:
                        rf[k] = rf[k] * penalty_scale

        # Activity level
        if activity_level:
            lvl = str(activity_level).strip().lower().replace("-", "_").replace(" ", "_")
            aliases = {
                "sedentary": "sedentary", "low": "sedentary", "very_low": "sedentary",
                "deskjob": "sedentary", "inactive": "sedentary",
                "light": "light", "lightly_active": "light", "mild": "light",
                "casual": "light", "walking": "light",
                "moderate": "moderate", "medium": "moderate", "average": "moderate",
                "balanced": "moderate", "normal": "moderate",
                "heavy": "heavy", "high": "heavy", "active": "heavy",
                "post_workout": "heavy", "training": "heavy", "workout": "heavy",
                "very_heavy": "very_heavy", "veryheavily": "very_heavy",
                "veryactive": "very_heavy", "athlete": "very_heavy", "intense": "very_heavy",
                "extreme": "very_heavy",
            }
            lvl = aliases.get(lvl, "moderate")
            am = ACTIVITY_MULTS[lvl]
            # multiply existing
            df = {k: df.get(k, 1.0) * am["default"].get(k, 1.0) for k in df.keys()}
            for rk, rm in am.get("rule_overrides", {}).items():
                if rk in rf:
                    rf[rk] = rf[rk] * rm

        return df, rf

    # ----------------- Scoring -----------------
    def calculate_dish_score(
        dish: Dict[str, Any],
        hunger_level: str,
        user_live_goal: Dict[str, Any],
        live_goal_energy: float,
        percentage_difference: Dict[str, float],  # kept for compatibility
        default_factors_eff: Dict[str, float],
        rule_factors_eff: Dict[str, float],
        *,
        hour_now: int,
        pro_goal_pct: float,
        carb_goal_pct: float,
        fat_goal_pct: float,
        fiber_goal_pct: float,
    ) -> float:
        epsilon = 1e-6

        serving_size = float(
            dish.get("dish_variants", {}).get("normal", {}).get("full", {}).get("serving", {}).get("size", 0.0)
        )
        macro_nutrients = (
            dish.get("dish_variants", {})
            .get("normal", {})
            .get("full", {})
            .get("calculate_nutrients", {})
            .get("macro_nutrients", [])
        )
        dish_nutrients_data = get_nutrients_data(dish)

        saturated_fat = dish_nutrients_data.get("FASAT", 0.0)
        polyunsat = dish_nutrients_data.get("FAPU", 0.0)
        monounsat = dish_nutrients_data.get("FAMU", 0.0)
        cholesterol = dish_nutrients_data.get("CHOLC", 0.0)
        sodium = dish_nutrients_data.get("NA", 0.0)
        sugar = dish_nutrients_data.get("TOTALFREESUGARS", 0.0)
        sugar_pct = (sugar * 100 / serving_size) if serving_size > 0 else 0.0

        distributed_percentage = dish.get("distributed_percentage", {}) or {}
        dish_pro_pct = safe_percentage(distributed_percentage.get("proteins"))
        dish_carb_pct = safe_percentage(distributed_percentage.get("carbs"))
        dish_fat_pct = safe_percentage(distributed_percentage.get("fats"))
        dish_fiber_pct = safe_percentage(distributed_percentage.get("fibers"))

        dish_energy = get_macro_value(macro_nutrients, "energy")
        dish_protein = get_macro_value(macro_nutrients, "proteins")
        dish_carbs = get_macro_value(macro_nutrients, "carbs")
        dish_fats = get_macro_value(macro_nutrients, "fats")
        dish_fibers = get_macro_value(macro_nutrients, "fibers")

        live_goal_protein = get_live_goal_value(user_live_goal, "protein", None, 22)
        live_goal_carbs = get_live_goal_value(user_live_goal, "carbs", None, 22)
        live_goal_fats = get_live_goal_value(user_live_goal, "fats", None, 22)
        live_goal_fibers = get_live_goal_value(user_live_goal, "fibers", "fiber", 22)

        # Effective factor weights (do NOT scale by percentage_difference)
        live_protein_factor = default_factors_eff.get("protein", 1)
        live_carbs_factor = default_factors_eff.get("carbs", 1)
        live_fats_factor = default_factors_eff.get("fats", 1)
        live_fiber_factor = default_factors_eff.get("fibers", 1)
        live_energy_factor = default_factors_eff.get("energy", 1)


        # Density
        def _ratio(x, tgt):
            if tgt <= 0:
                return 0.0
            return min(1.0, max(0.0, x / tgt))

        r_pro = _ratio(dish_pro_pct, pro_goal_pct)
        r_carb = _ratio(dish_carb_pct, carb_goal_pct)
        r_fats = _ratio(dish_fat_pct, fat_goal_pct)
        r_fiber = _ratio(dish_fiber_pct, fiber_goal_pct)
        r_kcal = _ratio(dish_energy, live_goal_energy)

        density_num = (
            live_protein_factor * r_pro +
            live_carbs_factor * r_carb +
            live_fats_factor * r_fats +
            live_fiber_factor * r_fiber +
            live_energy_factor * r_kcal
        )
        density_den = (
            live_protein_factor + live_carbs_factor + live_fats_factor +
            live_fiber_factor + live_energy_factor
        ) or 1.0
        density_score = (density_num / density_den) * 100.0

        # Euclidean-ish distance (0..100)
        eu_components = [
            (dish_protein, live_goal_protein, live_protein_factor),
            (dish_carbs, live_goal_carbs, live_carbs_factor),
            (dish_fats, live_goal_fats, live_fats_factor),
            (dish_fibers, live_goal_fibers, live_fiber_factor),
            (dish_energy, live_goal_energy, live_energy_factor),
        ]
        eu_score_sum = 0.0
        penalty_flag = False
        for actual, goal, weight in eu_components:
            dist = abs(actual - goal)
            if dist > 30:
                penalty_flag = True
            score = max(0, min(100, (1 - dist / (goal + 1e-6)) * 100))
            eu_score_sum += score * weight

        euclidean_distance_score = eu_score_sum / (
            live_protein_factor + live_carbs_factor + live_fats_factor + live_fiber_factor + live_energy_factor + 1e-6
        )
        if penalty_flag:
            euclidean_distance_score *= 0.8

        # Satiety
        energy_nonzero = dish_energy or 1.0
        satiety_raw = (dish_protein + dish_fibers) / energy_nonzero
        hunger_mult = {"High": 1.10, "Medium": 1.00, "Low": 0.95}.get(hunger_level, 1.0)
        satiety_component = min(1.0, satiety_raw * 3.0) * hunger_mult
        satiety_score = satiety_component * 100.0

        # Rules
        protein_overrule_score = protein_overrule(distributed_percentage)
        low_carbs_overrule_score = low_carbs_overrule(distributed_percentage)
        low_fat_overrule_score = low_fat_overrule(distributed_percentage)
        sugar_content_rule_score = sugar_content_rule(sugar_pct)
        sodium_content_rule_score = sodium_content_rule(sodium, serving_size)
        saturated_fat_rule_score = saturated_fat_rule(saturated_fat, serving_size)
        cholesterol_rule_score = cholesterol_rule(cholesterol, serving_size)
        caloric_density_rule_score = caloric_density_rule(dish_energy, serving_size)
        good_fats_score = good_fats_rule(saturated_fat, polyunsat, monounsat, serving_size)

        rules_weighted_sum = (
            protein_overrule_score * rule_factors_eff.get("protein_overrule_factor", 1) +
            low_carbs_overrule_score * rule_factors_eff.get("low_carbs_overrule_factor", 1) +
            low_fat_overrule_score * rule_factors_eff.get("low_fat_overrule_factor", 1) +
            sugar_content_rule_score * rule_factors_eff.get("sugar_content_factor", 1) +
            sodium_content_rule_score * rule_factors_eff.get("sodium_content_factor", 1) +
            saturated_fat_rule_score * rule_factors_eff.get("saturated_fat_factor", 1) +
            cholesterol_rule_score * rule_factors_eff.get("cholesterol_factor", 1) +
            caloric_density_rule_score * rule_factors_eff.get("caloric_density_factor", 1) +
            good_fats_score * rule_factors_eff.get("good_fats_factor", 1)
        )
        rules_den = sum(rule_factors_eff.values()) or 1.0
        rules_weighted_avg = rules_weighted_sum / rules_den  # 0..100

        # Timing alignment
        dish_cats = _norm_cat_set(dish.get("timing_category"))
        timing_score = timing_alignment_score(dish_cats, hour_now)
        allowed_now = timing_categories_for_hour(hour_now)

        # Final raw blend (current weights)
        w_density = default_factors_eff.get("density_factor", 0.00)
        w_eu = default_factors_eff.get("euclidean_factor", 0.00)
        w_sat = default_factors_eff.get("satiety_factor", 0.00)
        w_rules = default_factors_eff.get("rules_factor", 0.00)
        w_timing = default_factors_eff.get("timing_factor", 10.00)

        #print("density:", w_density, "euclidean:", w_eu, "satiety:", w_sat, "rules:", w_rules, "timing:", w_timing)
        
        pre_guardrail = (
            w_density * density_score +
            w_eu * euclidean_distance_score +
            w_sat * satiety_score +
            w_rules * rules_weighted_avg +
            w_timing * timing_score
        )

        # Apply guardrail first
        outsides = 0
        outsides += 0 if within_pct(dish_energy, live_goal_energy, 0.18) else 1
        outsides += 0 if within_pct(dish_protein, live_goal_protein, 0.22) else 1
        outsides += 0 if within_pct(dish_carbs, live_goal_carbs, 0.22) else 1
        outsides += 0 if within_pct(dish_fats, live_goal_fats, 0.25) else 1
        guard_mult = 0.70 if outsides >= 2 else (0.85 if outsides == 1 else 1.0)

        pre_guardrail *= guard_mult

        # 🔧 NEW: normalize by total weight so result is in 0..100-ish
        total_w = (w_density + w_eu + w_sat + w_rules + w_timing) or 1.0
        final_raw = pre_guardrail / total_w

        # Optional cap (keeps sanity)
        final_raw = max(0.0, min(100.0, final_raw))
        return float(final_raw)

    menu_data = db["RestaurantMenuData"].find_one({"_id": restro_id})
    user_data = db["UserData"].find_one({"_id": user_id})

    if not user_data:
        return []
    if "hunger_level" not in user_data or "goals" not in user_data:
        return []
    if not menu_data or "menu" not in menu_data:
        return []

    hunger_level = user_data["hunger_level"]
    default_goal_nutrients = user_data["goals"]["default_goal"]["nutrients"]
    user_live_goal = user_data["goals"]["live_goal"]["nutrients"]
    live_goal_energy = float(user_data["goals"]["live_goal"]["kcal"]["value"])

    # Percentage difference vs default goal (telemetry only)
    def _nut_val(dct, key_main, key_alt=None, default=0.0):
        if not isinstance(dct, dict):
            return default
        if key_main in dct and isinstance(dct[key_main], dict):
            return float(dct[key_main].get("value", default) or 0.0)
        if key_alt and key_alt in dct and isinstance(dct[key_alt], dict):
            return float(dct[key_alt].get("value", default) or 0.0)
        return default

    live_p = _nut_val(user_live_goal, "protein")
    live_c = _nut_val(user_live_goal, "carbs")
    live_f = _nut_val(user_live_goal, "fats")
    live_fib = _nut_val(user_live_goal, "fibers", "fiber")

    def_p = _nut_val(default_goal_nutrients, "protein")
    def_c = _nut_val(default_goal_nutrients, "carbs")
    def_f = _nut_val(default_goal_nutrients, "fats")
    def_fib = _nut_val(default_goal_nutrients, "fibers", "fiber")

    live_kcal = live_goal_energy
    def_kcal = float(user_data["goals"]["default_goal"]["kcal"]["value"])

    def pct(a, b):
        denom = b if abs(b) > 1e-6 else 1e-6
        return round(((a - b) / denom) * 100, 2)

    percentage_difference = {
        "proteins": pct(live_p, def_p),
        "carbs": pct(live_c, def_c),
        "fats": pct(live_f, def_f),
        "fibers": pct(live_fib, def_fib),
        "energy": pct(live_kcal, def_kcal),
    }

    # ---------------------------------------------------------------------
    # Context: current time/day + Birthday check (auto via UserData DOB)
    # ---------------------------------------------------------------------
    now = datetime.now()
    hour_now = now.hour
    weekday_now = now.weekday()
    birthday_today = is_user_birthday(user_data)

    live_goal_protein = _nut_val(user_live_goal, "protein")
    live_goal_carbs = _nut_val(user_live_goal, "carbs")
    live_goal_fats = _nut_val(user_live_goal, "fats")
    live_goal_fibers = _nut_val(user_live_goal, "fibers", "fiber")

    if live_goal_energy > 0:
        pro_goal_pct = (live_goal_protein * 4 / live_goal_energy) * 100.0
        carb_goal_pct = (live_goal_carbs * 4 / live_goal_energy) * 100.0
        fat_goal_pct = (live_goal_fats * 9 / live_goal_energy) * 100.0
        fiber_goal_pct = (live_goal_fibers * 2 / live_goal_energy) * 100.0
    else:
        pro_goal_pct = carb_goal_pct = fat_goal_pct = fiber_goal_pct = 0.0


    # Apply global context layers once (weather/meal/day + birthday + activity)
    raw_activity, activity_level = get_user_activity_level(db, user_id)

    default_factors_eff, rule_factors_eff = adjust_factors(
        default_factors.copy(),
        rule_factors.copy(),
        hour=hour_now,
        weekday=weekday_now,
        is_group=is_group_bool,
        apply_context_layers=True,
        special_occasion=birthday_today,
        activity_level=activity_level,
    )
    
    # ---------------------------------------------------------------------
    # Score dishes (per-dish cuisine + group flag; no extra global layers)
    # ---------------------------------------------------------------------
    scored_dishes: List[Dict[str, Any]] = []
    for dish in menu_data.get("menu", []):
        dish_id = dish.get("_id")
        if not dish_id:
            continue

        score = calculate_dish_score(
            dish,
            hunger_level,
            user_live_goal,
            live_goal_energy,
            percentage_difference,
            default_factors_eff,
            rule_factors_eff,
            hour_now=hour_now,
            pro_goal_pct=pro_goal_pct,
            carb_goal_pct=carb_goal_pct,
            fat_goal_pct=fat_goal_pct,
            fiber_goal_pct=fiber_goal_pct,
        )

        # Get macro snapshot (from nutrients array)
        dish_nutrients_data = get_nutrients_data(dish)
        macro_snapshot = {
            "energy": dish_nutrients_data.get("ENERC", 0.0),
            "protein": dish_nutrients_data.get("PROTCNT", 0.0),
            "carbs": dish_nutrients_data.get("CHOAVLDF", 0.0),
            "fats": dish_nutrients_data.get("FATCE", 0.0),
            "fibers": dish_nutrients_data.get("FIBTG", 0.0)
        }

        # Stash temporary fields for sorting/bucketing
        dish["__score"] = float(score)
        dish["macro_snapshot"] = macro_snapshot
        dish["timing_category"] = dish.get("timing_category", []) or []
        scored_dishes.append(dish)

    # --------------------------------------------------------------------- 
    # Sort & bucketize (Top 5% = Best, Next 5% = Good)
    # ---------------------------------------------------------------------
    scored_dishes.sort(key=lambda d: d.get("__score", 0.0), reverse=True)


    COURSE_SEQUENCE = ["Main Course", "Side Dish", "Salad", "Soup", "Starter", "Snack", "Drink", "Dessert"]
    COURSE_INDEX = {v: i for i, v in enumerate(COURSE_SEQUENCE)}
    DEFAULT_RANK = len(COURSE_SEQUENCE)

    def course_rank(d):
        dt = d.get("dish_type", [])
        if isinstance(dt, str):
            dt = [dt]
        elif not isinstance(dt, list):
            dt = []
        ranks = [COURSE_INDEX[t] for t in dt if t in COURSE_INDEX]
        return min(ranks) if ranks else DEFAULT_RANK  # <-- changed here


    total = len(scored_dishes)
    if total == 0:
        return json.dumps([])

    top_5 = max(1, round(total * 0.05))
    next_5 = max(0, round(total * 0.05))

    temp_list = scored_dishes[:top_5 + next_5] 
    
    # tie-break by name to keep it stable
    temp_list = sorted(temp_list, key=lambda x: (course_rank(x), x.get("dish_name", "").lower()))
    
    best_match = temp_list[:top_5]
    good_match = temp_list[top_5: top_5 + next_5]    

    # Tag and clean temporary fields; DO NOT return scores or helper keys.
    for d in best_match:
        d.pop("__score", None)
        d.pop("__cuisine_tmp", None)
        d["match"] = "Best Match"

    for d in good_match:
        d.pop("__score", None)
        d.pop("__cuisine_tmp", None)
        d["match"] = "Good Match"

    #list of name of dishes
    best_match = [d["dish_name"] for d in best_match]
    good_match = [d["dish_name"] for d in good_match]

    final_result = [
        {
            "category": "Best Match",
            "dishes": best_match
        },
        {
            "category": "Good Match",
            "dishes": good_match
        }
    ]
   
    return json.dumps(final_result, default=str)
