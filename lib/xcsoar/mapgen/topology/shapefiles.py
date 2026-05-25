# -*- coding: utf-8 -*-
import json
import os
import subprocess

from xcsoar.mapgen.georect import GeoRect
from xcsoar.mapgen.filelist import FileList

__cmd_ogr2ogr = "ogr2ogr"
__cmd_shptree = "shptree"


def __is_water_label_layer(layer):
    return layer.get("name", "").startswith("water_") and "label" in layer


def __has_label_name(name):
    if name is None:
        return False
    if not isinstance(name, str):
        name = str(name)
    return bool(name.strip())


def __write_utf8_cpg(layer, dir_temp):
    cpg_path = os.path.join(dir_temp, layer["name"] + ".cpg")
    with open(cpg_path, "w", encoding="ascii") as f:
        f.write("UTF-8")


def __filter_unnamed_features(layer, dir_temp):
    label_field = layer["label"]
    shp_path = os.path.join(dir_temp, layer["name"] + ".shp")
    geojson_path = shp_path + ".filter.json"
    cpg_path = os.path.join(dir_temp, layer["name"] + ".cpg")

    subprocess.check_call([__cmd_ogr2ogr, "-f", "GeoJSON", geojson_path, shp_path])

    with open(geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    features = data.get("features", [])
    total = len(features)
    data["features"] = [
        feature
        for feature in features
        if __has_label_name((feature.get("properties") or {}).get(label_field))
    ]
    kept = len(data["features"])

    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    if os.path.exists(cpg_path):
        os.unlink(cpg_path)

    subprocess.check_call(
        [
            __cmd_ogr2ogr,
            "-overwrite",
            "-f",
            "ESRI Shapefile",
            "-lco",
            "ENCODING=UTF-8",
            shp_path,
            geojson_path,
        ]
    )
    os.unlink(geojson_path)
    __write_utf8_cpg(layer, dir_temp)

    print(
        (
            "Filtered layer {layer}: kept {kept}/{total} features with names"
        ).format(layer=layer["name"], kept=kept, total=total)
    )
    return kept


def __filter_datasets(bounds, datasets):
    return [
        dataset
        for dataset in datasets
        if bounds.intersects(GeoRect(*dataset["bounds"]))
    ]


def __create_layer_from_dataset(bounds, layer, dataset, append, downloader, dir_temp):
    if not isinstance(bounds, GeoRect):
        raise TypeError

    data_dir = downloader.retrieve_extracted(dataset["name"] + ".7z")

    print(("Reading dataset {} ...".format(dataset["name"])))
    arg = [__cmd_ogr2ogr, "-skipfailures"]

    if append:
        arg.append("-update")
        arg.append("-append")
    else:
        arg.extend(["-select", layer["label"] if "label" in layer else ""])

    if "where" in layer:
        arg.extend(["-where", layer["where"]])

    arg.extend(
        [
            "-spat",
            str(bounds.left),
            str(bounds.bottom),
            str(bounds.right),
            str(bounds.top),
        ]
    )

    arg.append(dir_temp)
    arg.append(data_dir)

    arg.append(layer["layer"])
    arg.extend(["-nln", layer["name"]])

    subprocess.check_call(arg)


def __create_layer_index(layer, dir_temp):
    print(("Generating index file for layer {} ...".format(layer["name"])))
    subprocess.check_call(
        [__cmd_shptree, os.path.join(dir_temp, layer["name"] + ".shp")]
    )


def __create_layer(
    bounds, layer, datasets, downloader, dir_temp, files, index, compressed=False
):
    print(("Creating topology layer {} ...".format(layer["name"])))

    datasets = __filter_datasets(bounds, datasets)
    for i in range(len(datasets)):
        __create_layer_from_dataset(
            bounds, layer, datasets[i], i != 0, downloader, dir_temp
        )

    shp_path = os.path.join(dir_temp, layer["name"] + ".shp")
    if os.path.exists(shp_path) and __is_water_label_layer(layer):
        if __filter_unnamed_features(layer, dir_temp) == 0:
            for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix"):
                path = os.path.join(dir_temp, layer["name"] + ext)
                if os.path.exists(path):
                    os.unlink(path)
            return

    if os.path.exists(shp_path):
        __create_layer_index(layer, dir_temp)

        files.add(os.path.join(dir_temp, layer["name"] + ".shp"), compressed)
        files.add(os.path.join(dir_temp, layer["name"] + ".shx"), compressed)
        files.add(os.path.join(dir_temp, layer["name"] + ".dbf"), compressed)
        files.add(os.path.join(dir_temp, layer["name"] + ".prj"), compressed)
        cpg_path = os.path.join(dir_temp, layer["name"] + ".cpg")
        if os.path.exists(cpg_path):
            files.add(cpg_path, compressed)
        files.add(os.path.join(dir_temp, layer["name"] + ".qix"), compressed)
        index.append(layer)


def __create_index_file(dir_temp, index):
    file = open(os.path.join(dir_temp, "topology.tpl"), "w")
    try:
        file.write(
            "* filename, range, icon, label_index, r, g, b, pen_width, label_range, label_important_range, alpha\n"
        )
        for layer in index:
            file.write(
                layer["name"]
                + ","
                + str(layer["range"])
                + ",,"
                + ("1" if "label" in layer else "")
                + ","
                + layer["color"]
                + ","
                + str(layer.get("pen_width", 1))
                + ","
                + str(layer.get("label_range", layer["range"]))
                + ","
                + str(layer.get("label_important_range", 0))
                + ","
                + str(layer.get("alpha", 255))
                + "\n"
            )
    finally:
        file.close()
    return os.path.join(dir_temp, "topology.tpl")


def create(bounds, downloader, dir_temp, compressed=False, level_of_detail=3):
    topology = downloader.manifest()["topology"]
    layers = topology["layers"]
    datasets = topology["datasets"]

    files = FileList()
    index = []
    for layer in layers:
        if layer["level_of_detail"] <= level_of_detail:
            __create_layer(
                bounds,
                layer,
                datasets[layer["dataset"]],
                downloader,
                dir_temp,
                files,
                index,
                compressed,
            )

    files.add(__create_index_file(dir_temp, index), True)
    return files
