from models import *
from utils import *
import matplotlib.pyplot as plt
import torch
import torch.nn as nn
import argparse
import os
import shutil
import glob
import tqdm

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('run_name', type=str)
    parser.add_argument('--target', type=str, default='wasserstein')
    parser.add_argument('--data', type=str, default='gmm')
    parser.add_argument('--normalize', action='store_true')
    #parser.add_argument('--norm_in', action='store_true')
    #parser.add_argument('--norm_out', action='store_true')
    parser.add_argument('--scaleinv', action='store_true')
    parser.add_argument('--checkpoint_dir', type=str, default="/checkpoint/kaselby")
    parser.add_argument('--checkpoint_name', type=str, default=None)
    parser.add_argument('--scaling', type=float, default=0.5)
    parser.add_argument('--blur', type=float, default=0.05)
    parser.add_argument('--equi', action='store_true')

    return parser.parse_args()

def normalize_sets(*X):
    avg_norm = torch.cat(X, dim=1).norm(dim=-1,keepdim=True).mean(dim=1,keepdim=True)
    return [x / avg_norm for x in X], avg_norm

def evaluate(model, baselines, generator, label_fct, exact_loss=False, batch_size=64, sample_kwargs={}, label_kwargs={}, criterion=nn.L1Loss(), steps=5000, normalize=False):
    model_losses = []
    baseline_losses = {k:[] for k in baselines.keys()}
    with torch.no_grad():
        for i in tqdm.tqdm(range(steps)):
            if exact_loss:
                X, theta = generator(batch_size, **sample_kwargs)
                #if use_cuda:
                    #X = [x.cuda() for x in X]
                    #theta = [t.cuda() for t in theta]
                if normalize:
                    Xnorm, avg_norm = normalize_sets(*X)
                labels = label_fct(*theta, X=X[0], **label_kwargs).squeeze(-1)
            else:
                X = generator(batch_size, **sample_kwargs)
                #if use_cuda:
                    #X = [x.cuda() for x in X]
                if normalize:
                    Xnorm, avg_norm = normalize_sets(*X)
                labels = label_fct(*X, **label_kwargs)
            if normalize:
                out = model(*Xnorm).squeeze(-1)
                out *= avg_norm.squeeze(-1).squeeze(-1)
            else:
                out = model(*X).squeeze(-1)
            model_loss = criterion(out, labels)
            model_losses.append(model_loss.item())
            for baseline_name, baseline_fct in baselines.items():
                baseline_loss = criterion(baseline_fct(*X), labels)
                baseline_losses[baseline_name].append(baseline_loss.item())
    return sum(model_losses)/len(model_losses), {k:sum(v)/len(v) for k,v in baseline_losses.items()}

def train(model, sample_fct, label_fct, baselines={}, exact_loss=False, criterion=nn.L1Loss(), batch_size=64, steps=3000, lr=1e-5, 
    checkpoint_dir=None, output_dir=None, save_every=1000, sample_kwargs={}, label_kwargs={}, normalize=False):
    #model.train(True)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    losses = []
    initial_step=1
    if checkpoint_dir is not None:
        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)
        else:
            checkpoint_path = os.path.join(checkpoint_dir, "checkpoint.pt")
            if os.path.exists(checkpoint_path):
                load_dict = torch.load(checkpoint_path)
                model, optimizer, initial_step, losses = load_dict['model'], load_dict['optimizer'], load_dict['step'], load_dict['losses']

    for i in tqdm.tqdm(range(initial_step,steps+1)):
        optimizer.zero_grad()
        if exact_loss:
            X, theta = sample_fct(batch_size, **sample_kwargs)
            #if use_cuda:
                #X = [x.cuda() for x in X]
                #theta = [t.cuda() for t in theta]
            if normalize:
                X, avg_norm = normalize_sets(*X)
            labels = label_fct(*theta, X=X[0], **label_kwargs).squeeze(-1)
        else:
            X = sample_fct(batch_size, **sample_kwargs)
            #if use_cuda:
                #X = [x.cuda() for x in X]
            if normalize:
                X, avg_norm = normalize_sets(*X)
            labels = label_fct(*X, **label_kwargs)
        loss = criterion(model(*X).squeeze(-1), labels)
        loss.backward()
        optimizer.step()

        losses.append(loss.item())

        if i % save_every == 0 and checkpoint_dir is not None:
            checkpoint_path = os.path.join(checkpoint_dir, "checkpoint.pt")
            if os.path.exists(checkpoint_path):
                os.remove(checkpoint_path)
            torch.save({'model':model,'optimizer':optimizer, 'step': i, 'losses':losses}, checkpoint_path)

    model_loss, baseline_losses = evaluate(model, baselines, sample_fct, label_fct, exact_loss=exact_loss, 
        batch_size=batch_size, label_kwargs=label_kwargs, sample_kwargs=sample_kwargs, criterion=criterion, 
        steps=500, normalize=normalize)

    torch.save(model._modules['module'], os.path.join(output_dir,"model.pt"))  
    torch.save({'losses':losses, 'eval_losses':{'model':model_loss, **baseline_losses}}, os.path.join(output_dir,"logs.pt"))   

    return losses


if __name__ == '__main__':
    args = parse_args()
    run_dir = os.path.join("runs", args.run_name)
    '''if os.path.exists(run_dir):
        if args.overwrite:
            shutil.rmtree(run_dir)
        else:
            raise Exception("Folder exists and overwrite is set to false.")'''
    if not os.path.exists(run_dir):
        os.makedirs(run_dir)

    device = torch.device("cuda:0")

    model_kwargs={'ln':True, 'remove_diag':True, 'num_blocks':2, 'norm_in':False, 'norm_out':False}
    if args.equi:
        model=EquiMultiSetTransformer1(1,1, dim_hidden=32, **model_kwargs).to(device)
    else:
        DIM=32
        model=MultiSetTransformer1(DIM, 1,1, dim_hidden=256, **model_kwargs).to(device)

    batch_size=64
    steps=60000

    if torch.cuda.device_count() > 1:
        n_gpus = torch.cuda.device_count()
        print("Let's use", n_gpus, "GPUs!")
        model = nn.DataParallel(model)
        batch_size *= n_gpus
        steps = int(steps/n_gpus)
    sample_kwargs={'set_size':(10,150)}
    
    if args.equi:
        sample_kwargs['dims'] = (24,40)
    else:
        sample_kwargs['n'] = DIM

    if args.target == 'wasserstein':
        label_fct = wasserstein
        label_kwargs={'scaling':0.98, 'blur':0.001}
        baselines={'sinkhorn_default':wasserstein}
        exact_loss=False
        lr = 1e-3
    elif args.target == 'kl':
        label_fct = kl_mc
        label_kwargs={}
        baselines={'knn':kl_knn}
        exact_loss=True
        lr = 3e-5
        sample_kwargs['nu']=3
        sample_kwargs['mu0']=1
        sample_kwargs['s0']=0.5

    if args.data == 'gmm':
        generator = GaussianGenerator(num_outputs=2, scaleinv=args.scaleinv, variable_dim=args.equi, return_params=exact_loss)
    elif args.data == 'nf':
        generator = NFGenerator(32, 2, num_outputs=2, use_maf=False, variable_dim=args.equi, return_params=exact_loss)
    else:
        raise NotImplementedError("nf or gmm")

    losses = train(model, generator, label_fct, baselines=baselines, checkpoint_dir=os.path.join(args.checkpoint_dir, args.checkpoint_name), \
        exact_loss=exact_loss, output_dir=run_dir, criterion=nn.MSELoss(), steps=steps, lr=lr, batch_size=batch_size, \
        sample_kwargs=sample_kwargs, label_kwargs=label_kwargs, normalize=args.normalize)




'''
d=2
hs=32
nh=4
ln=True
n_blocks=2
model= EquiEncoder(hs, n_blocks, nh, ln).cuda()
losses=train(model, generate_gaussian_nd, wasserstein, criterion=nn.MSELoss(), steps=20000, lr=1e-3, n=2, set_size=(50,75))
'''


