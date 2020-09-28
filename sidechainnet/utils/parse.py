""" This script parses the raw ProteinNet files from
https://github.com/aqlaboratory/proteinnet. """

import itertools
import multiprocessing
import os
import pickle
from glob import glob

import numpy as np
import prody as pr


def load_ids_from_text_files(directory, training_set):
    """
    Given a directory where raw ProteinNet records are stored along with .ids
    files, reads and returns the contents of those files. Effectively returns
    a list of IDs associated with the training, validation, and test sets.
    """
    with open(os.path.join(directory, f"training_{training_set}_ids.txt"),
              "r") as trainf, open(os.path.join(directory, "validation_ids.txt"),
                                   "r") as validf, open(
                                       os.path.join(directory, "testing_ids.txt"),
                                       "r") as testf:
        train_ids = trainf.read().splitlines()
        valid_ids = validf.read().splitlines()
        test_ids = testf.read().splitlines()
        return train_ids, valid_ids, test_ids


def read_protein_from_file(file_pointer, include_tertiary):
    """Parses a single record from a text-based ProteinNet file as a dictionary.

    This function was originally written by Jeppe Hallgren, though I have made
    slight modifications. The most recent version is available here:
    https://github.com/biolib/openprotein/blob/master/preprocessing.py
    Because Mr. Hallgren's software caries an MIT license, I have included his
    copyright notice which describes the method below. All other portions of
    this software are licensed according to the LICENSE file in this
    repository's home directory.

    MIT License

    Copyright (c) 2018 Jeppe Hallgren

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in
    all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

    Args:
        file_pointer: Opened file object that contains ProteinNet text records
        include_tertiary: boolean, whether or not to parse atomic coordinates

    Returns:
        A dictionary containing various data entries for a single ProteinNet ID.

                ex:
                     { "id"          : "1A9U_1_A",
                       "primary"     : "MRYSKKKNACEWNA",
                       "evolutionary": np.ndarray(...),
                        ...
                       }
    """
    dict_ = {}
    _dssp_dict = {'L': 0, 'H': 1, 'B': 2, 'E': 3, 'G': 4, 'I': 5, 'T': 6, 'S': 7}
    _mask_dict = {'-': 0, '+': 1}

    while True:
        next_line = file_pointer.readline()
        if next_line == '[ID]\n':
            id_ = file_pointer.readline()[:-1]
            dict_.update({'id': id_})
        elif next_line == '[PRIMARY]\n':
            primary = file_pointer.readline()[:-1]
            dict_.update({'primary': primary})
        elif next_line == '[EVOLUTIONARY]\n':
            evolutionary = []
            for residue in range(21):
                evolutionary.append(
                    [float(step) for step in file_pointer.readline().split()])
            evolutionary = np.asarray(evolutionary).T
            dict_.update({'evolutionary': evolutionary})
        elif next_line == '[SECONDARY]\n':
            secondary = list([_dssp_dict[dssp] for dssp in file_pointer.readline()[:-1]])
            dict_.update({'secondary': secondary})
        elif next_line == '[TERTIARY]\n' and include_tertiary:
            tertiary = []
            # 3 dimension
            for axis in range(3):
                tertiary.append(
                    [float(coord) for coord in file_pointer.readline().split()])
            dict_.update({'tertiary': tertiary})
        elif next_line == '[MASK]\n':
            mask = list([_mask_dict[aa] for aa in file_pointer.readline()[:-1]])
            dict_.update({'mask': mask})
        elif next_line == '\n':
            return dict_
        elif next_line == '':
            return None


def process_file(input_filename_out_dir, return_ids=False):
    """
    A parallelizable method for processing one raw ProteinNet file and
    creating a pickled dictionary of the data.
    """
    all_ids = []
    input_filename, out_dir = input_filename_out_dir
    print("    " + input_filename)
    text_file = open(os.path.join(out_dir,
                                  os.path.basename(input_filename) + '_ids.txt'), "w")
    input_file = open(input_filename, "r")
    meta_dict = {}
    while True:
        next_protein = read_protein_from_file(input_file, include_tertiary=True)
        if next_protein is None:
            break
        id_ = next_protein["id"]
        del next_protein["id"]
        meta_dict.update({id_: next_protein})
        text_file.write(f"{id_}\n")
        if return_ids:
            all_ids.append(id_)
    with open(os.path.join(out_dir,
                           os.path.basename(input_filename) + ".pkl"), "wb") as f:
        pickle.dump(meta_dict, f)
    input_file.close()
    text_file.close()
    print(f"{input_filename} finished.")
    if return_ids:
        return (input_filename, all_ids)


def parse_raw_proteinnet(proteinnet_in_dir, proteinnet_out_dir, training_set):
    """Extracts and saves information for a single ProteinNet dataset.

    Preprocesses raw ProteinNet records by reading them and transforming them
    into PyTorch-saved dictionaries. Files are kept separate due to file size.
    For ease of inspection, the ProteinNet IDs are extracted and save as `.ids` files.

    Args:
        proteinnet_in_dir: Directory where all raw ProteinNet files are kept
        proteinnet_out_dir: Directory to save processed data
        training_set: Which thinning of ProteinNet is requested

    Returns:
        relevant_ids: A list of ProteinNet IDs from corresponding training_set
    """
    train_file = f"training_{training_set}.pkl"

    # If the desired ProteinNet dataset has already been processed, load its IDs
    if os.path.exists(os.path.join(proteinnet_out_dir, train_file)):
        print(f"Raw ProteinNet files already preprocessed ("
              f"{os.path.join(proteinnet_out_dir, train_file)}).")
        relevant_ids = retrieve_relevant_proteinnetids_from_files(
            proteinnet_out_dir, training_set)
        return relevant_ids

    # If the preprocessed ProteinNet dictionaries don't exist, create them.
    if not os.path.exists(proteinnet_out_dir):
        os.makedirs(proteinnet_out_dir)

    # Look for the target ProteinNet files
    if not os.path.isdir(os.path.join(proteinnet_in_dir, "targets")):
        print("There must be a subdirectory containing all protein targets with "
              "the name 'targets'.\nYou can download the .tgz file from the "
              "following link: http://predictioncenter.org/download_area/CASP12/targets"
              "/\n"
              "(replace 'CASP12' with the CASP version of interest and download "
              "the most recent, largest compressed file in the list.")
        raise ValueError("Could not find ProteinNet targets.")
    # Look for the raw ProteinNet files
    input_files = [
        f for f in glob(os.path.join(proteinnet_in_dir, "*[!.ids]"))
        if not os.path.isdir(f)
    ]
    assert len(input_files) == 8, (
        f"Looking for raw ProteinNet files in '{proteinnet_in_dir}', but"
        "could not find all 8.\n Please download from Mohammed "
        "AlQuraishi's repository: "
        "https://github.com/aqlaboratory/proteinnet")

    # Process each ProteinNet file by turning them into PyTorch saved dictionaries
    print("Preprocessing raw ProteinNet files...")
    with multiprocessing.Pool(multiprocessing.cpu_count()) as p:
        p.map(process_file, zip(input_files, itertools.repeat(proteinnet_out_dir)))
    print(f"Done. Processed ProteinNet files saved to {proteinnet_out_dir}.")

    # Return the ProteinNet IDs associated with the target dataset
    relevant_ids = retrieve_relevant_proteinnetids_from_files(proteinnet_out_dir,
                                                              training_set)
    return relevant_ids


def retrieve_relevant_proteinnetids_from_files(proteinnet_out_dir, training_set):
    """Returns a list of ProteinNet IDs relevant for a particular training set.

    Args:
        proteinnet_out_dir: Directory containing preprocessed ProteinNet files.
        training_set: Which training set thinning of CASP to use.

    Returns:
        A list of ProteinNet IDs (training, validation, and test set).
    """
    train_file = f"training_{training_set}.pkl"
    relevant_training_file = os.path.join(proteinnet_out_dir,
                                          train_file.replace(".pkl", "_ids.txt"))
    relevant_id_files = [
        os.path.join(proteinnet_out_dir, "testing_ids.txt"),
        os.path.join(proteinnet_out_dir, "validation_ids.txt"), relevant_training_file
    ]
    relevant_ids = []
    for fname in relevant_id_files:
        with open(fname, "r") as f:
            relevant_ids += f.read().splitlines()

    return relevant_ids


def parse_astral_summary_file(lines):
    """
    Given a path to the ASTRAL database summary file, this function parses
    that file and returns a dictionary that maps ASTRAL IDs to (pdbid, chain).
    """
    d = {}
    for line in lines:
        if line.startswith("#"):
            continue
        line_items = line.split()
        if line_items[3] == "-":
            continue
        if line_items[3] not in d.keys():
            d[line_items[3]] = (line_items[4], line_items[5])
    return d


def get_chain_from_astral_id(astral_id, d):
    """
    Given an ASTRAL ID and the ASTRAL->PDB/chain mapping dictionary,
    this function attempts to return the relevant, parsed ProDy object.
    """
    pdbid, chain = d[astral_id]
    assert "," not in chain, f"Issue parsing {astral_id} with chain {chain} and pdbid " \
                             f"{pdbid}."
    chain, resnums = chain.split(":")

    if astral_id == "d4qrye_":
        chain = "A"

    a = pr.parsePDB(pdbid, chain=chain)
    if resnums != "":
        if resnums[0] == "-":
            # Ranges with negative numbers must be escaped with ` character
            a = a.select(f"resnum `{resnums[0] + resnums[1:].replace('-', ' to ')}`")
        else:
            a = a.select(f"resnum {resnums.replace('-', ' to ')}")
    return a