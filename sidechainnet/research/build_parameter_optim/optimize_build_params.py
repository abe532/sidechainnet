"""Load SC_HBUILD_INFO and optimize the corresponding build_params to minimize energy."""
import copy
from distutils.command.build import build
import pickle
import numpy as np
import pkg_resources
import matplotlib.pyplot as plt
import seaborn as sns

from tqdm import tqdm
import torch
from sidechainnet.structure.build_info import BB_BUILD_INFO, SC_HBUILD_INFO
from sidechainnet.structure.fastbuild import get_all_atom_build_params
from sidechainnet.utils.openmm_loss import OpenMMEnergyH
from sidechainnet.dataloaders.SCNProtein import OPENMM_FORCEFIELDS, SCNProtein
from sidechainnet.examples import alphabet_protein


class BuildParamOptimizer(object):
    """Class to help optimize building parameters."""

    def __init__(self,
                 protein,
                 opt_bond_lengths=True,
                 opt_thetas=True,
                 opt_chis=True,
                 ffname=OPENMM_FORCEFIELDS):
        self.build_params = get_all_atom_build_params(SC_HBUILD_INFO, BB_BUILD_INFO)
        self._starting_build_params = copy.deepcopy(self.build_params)
        self.protein = self.prepare_protein(protein)
        self.ffname = ffname
        self.energy_loss = OpenMMEnergyH()
        self.keys_to_optimize = []
        if opt_bond_lengths:
            self.keys_to_optimize.append('bond_lengths')
        if opt_thetas:
            self.keys_to_optimize.append('thetas')
        if opt_chis:
            self.keys_to_optimize.append('chis')

        self.losses = []

        # Create chis and thetas tensors from sin/cos values for optimization, since
        # sins and cosines cannot be optimized directly.
        for root_atom in ['N', 'CA', 'C']:
            if "thetas" in self.keys_to_optimize:
                self.build_params[root_atom]["thetas"] = torch.atan2(
                    self.build_params[root_atom]["sthetas"],
                    self.build_params[root_atom]["cthetas"])
            if "chis" in self.keys_to_optimize:
                self.build_params[root_atom]["chis"] = torch.atan2(
                    self.build_params[root_atom]["schis"],
                    self.build_params[root_atom]["cchis"])

        self.params = self.create_param_list_from_build_params(self.build_params)

    @staticmethod
    def to_sin_cos(angles):
        """Convert angles to sin and cos values."""
        return torch.sin(angles), torch.cos(angles)

    def prepare_protein(self, protein: SCNProtein):
        """Prepare protein for optimization by building hydrogens/init OpenMM."""
        protein.sb = None
        protein.angles.requires_grad_()
        protein.fastbuild(add_hydrogens=True,
                          build_params=self.build_params,
                          inplace=True)
        protein._initialize_openmm(nonbonded_interactions=False)
        return protein

    def create_param_list_from_build_params(self, build_params):
        """Extract optimizable parameters from full build_params dictionary."""
        params = []
        for root_atom in ['N', 'CA', 'C']:
            for param_key in self.keys_to_optimize:
                build_params[root_atom][param_key].requires_grad_()
                params.append(build_params[root_atom][param_key])
        return params

    def update_complete_build_params_with_optimized_params(self, optimized_params):
        """Update fill build_params dictionary with the optimized subset."""
        i = 0
        for root_atom in ['N', 'CA', 'C']:
            for param_key in self.keys_to_optimize:
                if param_key == "thetas":
                    self.build_params[root_atom]["sthetas"], self.build_params[root_atom][
                        "cthetas"] = self.to_sin_cos(optimized_params[i])
                elif param_key == "chis":
                    self.build_params[root_atom]["schis"], self.build_params[root_atom][
                        "cchis"] = self.to_sin_cos(optimized_params[i])
                else:
                    self.build_params[root_atom][param_key] = optimized_params[i]
                i += 1

    def save_build_params(self, path):
        """Write out build_params dict to path as pickle object."""
        with open(path, "wb") as f:
            pickle.dump(self.build_params, f)

    def optimize(self, opt='LBFGS', lr=1e-5, steps=100):
        """Optimize self.build_params to minimize OpenMMEnergyH."""
        to_optim = self.params
        p = self.protein

        self.build_params_history = [copy.deepcopy(self.params)]
        pbar = tqdm(range(steps), dynamic_ncols=True)

        # LBFGS Loop
        if opt == 'LBFGS':
            # TODO Fails to optimize
            self.opt = torch.optim.LBFGS(to_optim, lr=lr)
            for i in tqdm(range(steps)):
                # Note keeping
                self.build_params_history.append(
                    [copy.deepcopy(p.detach().cpu()) for p in to_optim])

                def closure():
                    self.opt.zero_grad()
                    # Update the build_params complete dict with the optimized values
                    self.update_complete_build_params_with_optimized_params(to_optim)
                    # Rebuild the protein
                    p.fastbuild(build_params=self.build_params,
                                add_hydrogens=True,
                                inplace=True)
                    loss = self.energy_loss.apply(p, p.hcoords)
                    loss.backward()
                    loss_np = float(loss.detach().numpy())
                    p._last_loss = loss_np
                    return loss

                self.opt.step(closure)
                pbar.set_postfix({'loss': f"{p._last_loss:.2f}"})

        # SGD Loop
        elif opt == 'SGD' or opt == 'adam':
            if opt == 'adam':
                self.opt = torch.optim.Adam(to_optim, lr=lr)
            elif opt == "SGD":
                self.opt = torch.optim.SGD(to_optim, lr=lr, momentum=0.9)
            scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(self.opt,
                                                                   'min',
                                                                   verbose=True,
                                                                   threshold=1e-4,
                                                                   threshold_mode='abs',
                                                                   factor=.5,
                                                                   patience=20)
            best_loss = None
            best_params_so_far = None
            counter = 0
            patience = 20
            epsilon = 1e-4
            for i in pbar:
                # Note keeping
                self.build_params_history.append(
                    [copy.deepcopy(p.detach().cpu()) for p in to_optim])
                self.opt.zero_grad()

                # Update the build_params complete dict with the optimized values
                self.update_complete_build_params_with_optimized_params(to_optim)

                # Rebuild the protein
                p.fastbuild(build_params=self.build_params,
                            add_hydrogens=True,
                            inplace=True)

                # Compute the new energy
                loss = self.energy_loss.apply(p, p.hcoords)
                loss.backward()
                lossnp = float(loss.detach().cpu().numpy())
                if (best_loss is None or
                    (lossnp < best_loss and np.abs(best_loss - lossnp) > epsilon)):
                    best_loss = lossnp
                    best_params_so_far = copy.deepcopy(to_optim)
                    counter = 0
                elif counter > patience:
                    print("Stopping early.")
                    break
                elif counter < patience:
                    counter += 1
                self.losses.append(lossnp)
                self.opt.step()
                scheduler.step(lossnp)

                pbar.set_postfix({'loss': f"{lossnp:.2f}"})

        sns.lineplot(data=self.losses)
        plt.title("Protein Potential Energy with BuildParams")
        plt.xlabel("Optimization Step")
        plt.savefig(
            pkg_resources.resource_filename("sidechainnet",
                                            "resources/build_params.pkl").replace(
                                                "pkl", "png"))

        self.update_complete_build_params_with_optimized_params(best_params_so_far)
        return self.build_params


def main():
    """Minimize the build parameters for an example alphabet protein."""
    p = alphabet_protein()
    bpo = BuildParamOptimizer(p,
                              opt_bond_lengths=True,
                              opt_thetas=True,
                              opt_chis=True,
                              ffname=OPENMM_FORCEFIELDS)
    # build_params = bpo.optimize(opt='SGD', lr=1e-6, steps=100000)
    build_params = bpo.optimize(opt='adam', lr=1e-3, steps=25000)
    # build_params = bpo.optimize(opt='LBFGS', lr=1e-4, steps=1000)
    fn = pkg_resources.resource_filename("sidechainnet", "resources/build_params.pkl")
    with open(fn, "wb") as f:
        pickle.dump(build_params, f)


if __name__ == "__main__":
    main()
