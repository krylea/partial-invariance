from utils import *
import os
import argparse

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('run_name', type=str)
    parser.add_argument('--basedir', type=str, default="final-runs")
    parser.add_argument('--n', type=int, default=2)

    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()

    model = torch.load(os.path.join(args.basedir, "mi", args.run_name))
    generator = CorrelatedGaussianGenerator(return_params=True)

    N=100
    set_size=(100,150)
    rho = torch.tensor([-0.99,-0.9,-0.7-0.5,-0.3,-0.1,0,0.1,0.3,0.5,0.7,0.9,0.99], device=model.device)
    mi_true = mi_corr_gaussian(rho, d=args.n)
    mi_model = torch.zeros_like(rho)
    mi_kraskov = torch.zeros_like(rho)
    for i in range(rho.size(0)):
        X, T = generator(N, n=args.n, corr=rho[i])
        mi_model[i] = model(*X).squeeze(-1).detach().cpu()
        mi_kraskov[i] = kraskov_mi1(*X)

    torch.save({'rho':rho, 'true':mi_true, 'model':mi_model, 'kraskov':mi_kraskov}, os.path.join(args.basedir, "mi", args.run_name, "rho.pt"))
