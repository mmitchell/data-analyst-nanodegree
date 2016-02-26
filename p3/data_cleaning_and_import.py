#!/usr/bin/env python
# -*- coding: utf-8 -*-

from collections import defaultdict
import xml.etree.cElementTree as ET
from pymongo import MongoClient
import json
import codecs
import re
from pprint import pprint

#####
# Helpful constants
#####

# Map file
#MAP_FILE = 'sample.osm'
MAP_FILE = 'indianapolis_indiana.osm'

# Useful Regexes
lower = re.compile(r'^([a-z]|_)*$')
lower_colon = re.compile(r'^([a-z]|_)*:([a-z]|_)*$')
problemchars = re.compile(r'[=\+/&<>;\'"\?%#$@\,\. \t\r\n]')
street_type_re = re.compile(r'\b\S+\.?$', re.IGNORECASE)

# Lists to help data shape conversion
CREATED = [ "version", "changeset", "timestamp", "user", "uid"]

EXPECTED_STREET_NAMES = ["Street", "Avenue", "Boulevard", "Drive", "Court", "Place", "Square", "Lane", "Road",
                         "Trail", "Parkway", "Pass", "Pike", "Commons", "Center", "Circle", "Crossing", "N", "North", "E",
                         "East", "S", "South", "Southwest", "W", "West", "Point", "Row", "Run", "Trace", "Walk", "Way"]

STREET_NAME_MAPPINGS = { "Ave":   "Avenue",
                         "Blvd.": "Boulevard",
                         "Dr":    "Drive",
                         "Dr.":   "Drive",
                         "Ln":    "Lane",
                         "Pkwy":  "Parkway",
                         "Rd":    "Road",
                         "Rd.":   "Road",
                         "St":    "Street"}

CITY_NAME_MAPPINGS = { "Indianapolis ":         "Indianapolis",
                       "Indianapolis, Indiana": "Indianapolis",
                       "Indianapoliss":         "Indianapolis",
                       "Indianpolis":           "Indianapolis",
                       "Inidanapaolis":         "Indianapolis",
                       "indianapolis":          "Indianapolis",
                       "carmel":                "Carmel",
                       "greenwood":             "Greenwood",
                       "speedway":              "Speedway"}

#####
# Exploratory functions to check OSM file for tag counts, problem characters,
#   street names, postal codes, etc.
#####

def count_tags():
    tags = defaultdict(int)
    for event, elem in ET.iterparse(MAP_FILE):
        tags[elem.tag] += 1

    return tags

def check_key_types():
    keys = {"lower": 0, "lower_colon": 0, "problemchars": 0, "other": 0}
    for _, element in ET.iterparse(MAP_FILE):
        if element.tag == "tag":
            k = element.attrib['k']
            if re.search(lower, k):
                keys['lower'] += 1
            elif re.search(lower_colon, k):
                keys['lower_colon'] += 1
            elif re.search(problemchars, k):
                keys['problemchars'] += 1
            else:
                keys['other'] += 1

    return keys

def audit_street_type(street_types, street_name):
    m = street_type_re.search(street_name)
    if m:
        street_type = m.group()
        if street_type not in EXPECTED_STREET_NAMES:
            street_types[street_type].add(street_name)


def is_street_name(elem):
    return (elem.attrib['k'] == "addr:street")

def audit_city_name(city_names, city_name):
    city_names.add(city_name)

def is_city_name(elem):
    return (elem.attrib['k'] == "addr:city")

def audit(osmfile):
    osm_file = open(osmfile, "r")
    street_types = defaultdict(set)
    city_names = set()
    for event, elem in ET.iterparse(osm_file, events=("start",)):
        if elem.tag == "node" or elem.tag == "way":
            for tag in elem.iter("tag"):
                if is_street_name(tag):
                    audit_street_type(street_types, tag.attrib['v'])
                elif is_city_name(tag):
                    audit_city_name(city_names, tag.attrib['v'])
    osm_file.close()
    return {"street_types": street_types, "city_names:": city_names}


#####
# Functions to read the OSM file, sanitize the data, and import it into mongo db
#   note: due to the size of the file, these functions stream tags directly into
#         mongodb while sanitizing instead of writing to an intermediate file.
#####

# call eg.: better_name = update_name(name, STREET_NAME_MAPPINGS)

def update_street_name(name, mapping):
    for key in mapping:
        if key == name.split()[-1]:
            name = name.replace(key, mapping[key])

    return name

def update_city_name(name, mapping):
    for key in mapping:
        if key == name:
            name = mapping[key]

    return name

def shape_element(element):
    if element.tag in ['node', 'way']:
        node = {'created': {}, 'pos': [None, None]}
        node['type'] = element.tag

        # Shape tag attributes
        for key in element.attrib:
            if key in CREATED:
                node['created'][key] = element.attrib[key]
            elif key  == 'lat':
                node['pos'][0] = float(element.attrib[key])
            elif key  == 'lon':
                node['pos'][1] = float(element.attrib[key])
            else:
                node[key] = element.attrib[key]

        # Shape nested tag key/value attributes
        for tag in element.iter("tag"):
            if tag.attrib['k'].startswith('addr:'):
                if tag.attrib['k'].count(':') == 1:
                    if 'address' not in node:
                        node['address'] = {}

                    # Update street and city names if needed
                    value = tag.attrib['v']
                    if is_street_name(tag):
                        value = update_street_name(value, STREET_NAME_MAPPINGS)
                    if is_city_name(tag):
                        value = update_city_name(value, CITY_NAME_MAPPINGS)

                    node['address'][tag.attrib['k'].replace('addr:', '')] = value
            else:
                node[tag.attrib['k']] = tag.attrib['v']

        return node
    else:
        return None

def process_map(filename):
    indy = get_db().indianapolis
    indy.drop() # Drop the collection before we reimport the data
    for _, element in ET.iterparse(filename):
        el = shape_element(element)
        if el:
            indy.insert(el)

def get_db():
    client = MongoClient('localhost:27017')
    db = client.openstreetmap
    return db


#####
# Mongodb queries to get a brief statistical overview of the data
#####

def print_stats():
    indianapolis = get_db().indianapolis

    print 'total records:'
    print indianapolis.count()

    print 'nodes count:'
    print indianapolis.find({"type":"node"}).count()

    print 'ways count:'
    print indianapolis.find({"type":"way"}).count()

    print 'top 5 streets in addresses:'
    top_streets = indianapolis.aggregate([{"$match": {"address.street":{"$exists":1}}},
                            {"$group": {"_id": "$address.street", "count":{"$sum":1}}},
                            {"$sort": {"count": -1}},
                            {"$limit": 5}])
    print list(top_streets)

    print 'top 5 cities in addresses:'
    top_cities = indianapolis.aggregate([{"$match": {"address.city":{"$exists":1}}},
                            {"$group": {"_id": "$address.city", "count":{"$sum":1}}},
                            {"$sort": {"count": -1}},
                            {"$limit": 5}])
    print list(top_cities)


#####
# Main
#####

if __name__ == "__main__":
    print_stats()
    # pprint(audit(MAP_FILE))
    # pprint(count_tags())
    # pprint(check_key_types())
    # process_map(MAP_FILE)
