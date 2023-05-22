import torch
import os
import pandas as pd
import math
import numpy as np
import dendropy
from Bio import SeqIO
from torch.autograd import Function
import geoopt.manifolds.stereographic.math as pmath
import geoopt


def get_seq_length(args):
    backbone_seq_file = args.backbone_seq_file
    backbone_tree_file = args.backbone_tree_file
    if backbone_seq_file:
        seq = SeqIO.to_dict(SeqIO.parse(backbone_seq_file, "fasta"))
        args.sequence_length = len(list(seq.values())[0])
    else:
        args.sequence_length = 4
    tree = dendropy.Tree.get(path=backbone_tree_file, schema='newick')
    num_nodes = len(tree.leaf_nodes())
    if args.embedding_size == -1:
        args.embedding_size = 2 ** math.floor(math.log2(10 * num_nodes ** (1 / 2)))


def distance_portion(nodes1, nodes2, mode):
    if len(nodes1.shape) == 1:
        nodes1 = nodes1.unsqueeze(0)
    if len(nodes2.shape) == 1:
        nodes2 = nodes2.unsqueeze(0)
    n1 = len(nodes1)
    n2 = len(nodes2)
    nodes1 = nodes1.view(n1, 1, -1)
    nodes2 = nodes2.view(1, n2, -1)
    if mode == 'ms':
        return torch.sum((nodes1 - nodes2) ** 2, dim=-1)
    elif mode == 'L2':
        # breakpoint()
        return (torch.sum((nodes1 - nodes2) ** 2, dim=-1) + 1e-6).sqrt()
    elif mode == 'L1':
        return torch.sum(abs(nodes1 - nodes2), dim=-1)
    elif mode == 'cosine':
        return 1 - torch.nn.functional.cosine_similarity(nodes1, nodes2, dim=-1)
    elif mode == 'tan':
        cosine = torch.nn.functional.cosine_similarity(nodes1, nodes2, dim=-1)
        return (1 - cosine ** 2) / (cosine + 1e-9)
    elif mode == 'hyperbolic':
        nodes1 = nodes1.squeeze(1)
        nodes2 = nodes2.squeeze(0)
        # breakpoint()
        # nodes1 = nodes1 / (nodes1.norm(-1, keepdim=True) / (1 - 1e-4))
        # nodes2 = nodes2 / (nodes2.norm(-1, keepdim=True) / (1 - 1e-4))
        # return hyperbolic_dist(nodes1, nodes2)
        # ms_x = (nodes1 ** 2).sum(-1)
        # ms_y = (nodes2 ** 2).sum(-1)
        # d = - ((1 + ms_x)*(1 + ms_y)).sqrt() + (nodes1 * nodes2).sum(-1)
        # d = torch.acosh(-d + 1e-15)
        # return d
        return hyp_dist(nodes1, nodes2)


def project_hyperbolic(x):
    N, d = x.shape[0], x.shape[1]

    hnorm_x = torch.norm(x, dim=1, p=2, keepdim=True)

    s1 = torch.cosh(hnorm_x)
    s2 = torch.div(torch.sinh(hnorm_x), hnorm_x)

    e = torch.zeros(N, d + 1).to(x.device)
    e[:, 0] = 1

    zero_col = torch.zeros(N, 1).to(x.device)
    z = torch.cat((zero_col, x), 1)
    z = torch.mul(s1, e) + torch.mul(s2, z)  # hyperbolic embeddings
    return z


def hyp_dist(embeddings1, embeddings2=None):
    if embeddings2 is None:
        x1 = x2 = project_hyperbolic(embeddings1)
    else:
        x1, x2 = project_hyperbolic(embeddings1), project_hyperbolic(embeddings2)
    d = x1.shape[1] - 1
    H = torch.eye(d + 1, d + 1).to(x1.device)
    H[0, 0] = -1
    N1, N2 = x1.shape[0], x2.shape[0]
    G = torch.matmul(torch.matmul(x1, H), torch.transpose(x2, 0, 1))
    G[G >= -1] = -1
    return torch.acosh(-G)


def hyp_dist_tmp(embeddings, formula, return_coord=True):
    # N = embeddings.weight.size()[0]
    # x = embeddings( torch.arange(0, N) )
    # D = torch.cdist(x, x, p = 2)
    # D = torch.pow(D, 2)
    # print(embeddings.weight)

    # norm_x = 1-torch.sum(x**2, dim=-1, keepdim=True)

    # D = torch.div(D, norm_x)
    # D = torch.transpose(D, 0, 1)
    # D = torch.div(D, norm_x)
    # D = D[torch.triu(torch.ones(N,N),diagonal = 1) == 1]
    # D = torch.acosh(1+2*D)
    # breakpoint()
    N = embeddings.size()[0]
    d = embeddings.size()[1]
    x = embeddings
    hnorm_x = torch.norm(x, dim=1, p=2, keepdim=True)

    s1 = torch.cosh(hnorm_x)
    s2 = torch.div(torch.sinh(hnorm_x), hnorm_x)

    e = torch.zeros(N, d + 1).to(embeddings.device)
    e[:, 0] = 1
    H = torch.eye(d + 1, d + 1).to(embeddings.device)
    H[0, 0] = -1

    zero_col = torch.zeros(N, 1).to(embeddings.device)
    z = torch.cat((zero_col, x), 1)
    z = torch.mul(s1, e) + torch.mul(s2, z)  # hyperbolic embeddings

    G = torch.matmul(torch.matmul(z, H), torch.transpose(z, 0, 1))
    # G = G[torch.triu(torch.ones(N, N), diagonal=1) == 1]
    # G = G - 1e-5
    # tmp = G[torch.triu(torch.ones(N, N), diagonal=1)==1]
    # print((tmp >= -1).sum() / (N * N - N))
    if formula == 1:
        G[G >= -1] = -1  # log this
        try:
            return torch.acosh(-G), (G[torch.from_numpy(np.triu(np.ones((N, N)), 1) == 1)] >= -1).sum() / (N-1)**2
        except:
            breakpoint()
    else:
        return -G - 1, (G[np.triu(np.ones((N, N)), 1) == 1] >= -1).sum() / (N-1)**2


def loss(D, embeddings, lr, scale, formula):  # loss(self, triple_ids, similarities): # commented by Puoya
    """Computes the HypHC loss.
    Args:
        triple_ids: B x 3 tensor with triple ids
        similarities: B x 3 tensor with pairwise similarities for triples
                      [s12, s13, s23]
    """
    # commented by Puoya
    # e1 = self.embeddings(triple_ids[:, 0])
    # e2 = self.embeddings(triple_ids[:, 1])
    # e3 = self.embeddings(triple_ids[:, 2])
    # e1 = self.normalize_embeddings(e1)
    # e2 = self.normalize_embeddings(e2)
    # e3 = self.normalize_embeddings(e3)
    # d_12 = hyp_lca(e1, e2, return_coord=False)
    # d_13 = hyp_lca(e1, e3, return_coord=False)
    # d_23 = hyp_lca(e2, e3, return_coord=False)
    # lca_norm = torch.cat([d_12, d_13, d_23], dim=-1)
    # weights = torch.softmax(lca_norm / self.temperature, dim=-1)
    # w_ord = torch.sum(similarities * weights, dim=-1, keepdim=True)
    # total = torch.sum(similarities, dim=-1, keepdim=True) - w_ord
    # return torch.mean(total)
    N = D.shape[0]
    distances, num_one = hyp_dist_tmp(embeddings, formula)
    distances = distances[torch.from_numpy(np.triu(np.ones((N, N)), 1) == 1)]
    # distances = euc_dist(self.embeddings)
    D_vec = D[torch.from_numpy(np.triu(np.ones((N, N)), 1) == 1)]
    # D_vec = torch.tensor(D_vec)
    if formula == 2:
        D_vec = torch.cosh(D_vec * 1 / scale) - 1
    r = 1
    # r = 0.39073709165622195/5.873781526199998
    # print(flag)
    # if flag:
    if formula == 1:
        cost = torch.sqrt(torch.mean(torch.pow(torch.div(scale * r * distances, D_vec + 1e-12) - 1, 2)))
        #divd_sqrt = (scale * r * distances/D_vec) ** (1/2)
        #cost = torch.mean(D_vec**(-3/2) * ((divd_sqrt - 1) ** 2)).sqrt()
#        weight = 1 / (D_vec ** 2)
#        dev = ((scale * r * distances)**0.5 - D_vec**0.5)**2
#        cost = torch.mean(weight * dev)
    else:
        cost = torch.sqrt(torch.mean(torch.pow(torch.div(distances, D_vec) - 1, 2)))
    a = torch.mean(torch.div(r * distances, D_vec+1e-12) + 10 ** (-10))
    b = torch.mean(torch.pow(torch.div(r * distances, D_vec+1e-12), 2) + 10 ** (-10))
    scale = (1 - lr) * scale + lr * (a.item() / b.item())
    # print(flag)
    # print(scale)
    # cost =  torch.sqrt(torch.mean( torch.pow( torch.div(distances, D_vec)-1, 2) ) )
    # cost = torch.sqrt( torch.mean ( torch.pow(scale*distances- D_vec,2) ) )
    # grad = torch.mean(torch.mul( torch.div(distances,D_vec), torch.div(scale*distances,D_vec)-1))

    return cost, distances, scale, num_one


def jc_dist(seqs1_c, seqs2, names1, names2):
    seqs1_tmp = np.zeros(seqs1_c.shape)
    seqs2_tmp = np.zeros(seqs2.shape)
    seqs1_tmp[seqs1_c == 'A'] = 0
    seqs1_tmp[seqs1_c == 'C'] = 1
    seqs1_tmp[seqs1_c == 'G'] = 2
    seqs1_tmp[seqs1_c == 'T'] = 3
    seqs1_tmp[seqs1_c == '-'] = 4
    seqs2_tmp[seqs2 == 'A'] = 0
    seqs2_tmp[seqs2 == 'C'] = 1
    seqs2_tmp[seqs2 == 'G'] = 2
    seqs2_tmp[seqs2 == 'T'] = 3
    seqs2_tmp[seqs2 == '-'] = 4
    seqs1_c = seqs1_tmp
    seqs2 = seqs2_tmp

    n2, l = seqs2.shape[0], seqs2.shape[-1]
    seqs2 = seqs2.reshape(1, n2, -1)
    hamming_dist = []
    for i in range(math.ceil(len(seqs1_c) / 1000)):
        seqs1 = seqs1_c[i * 1000: (i + 1) * 1000]
        n1 = seqs1.shape[0]
        seqs1 = seqs1.reshape(n1, 1, -1)
        # breakpoint()
        non_zero = np.logical_and(seqs1 != 4, seqs2 != 4)
        hd = (seqs1 != seqs2) * non_zero
        hd = np.count_nonzero(hd, axis=-1)
        hamming_dist.append(hd / np.count_nonzero(non_zero, axis=-1))
    hamming_dist = np.concatenate(hamming_dist, axis=0)
    jc = - 3 / 4 * np.log(1 - 4 / 3 * hamming_dist)
    jc_df = pd.DataFrame(dict(zip(names2, jc)))
    jc_df.index = names1
    return jc_df


def distance(nodes1, nodes2, mode):
    # node1: query
    # node2: backbone
    dist = []
    # np.save('query_emb.npy', np.array(nodes1.cpu()))
    # np.save('backbone_emb.npy', np.array(nodes2.cpu()))
    for i in range(math.ceil(len(nodes1) / 1000.0)):
        dist.append(distance_portion(nodes1[i * 1000: (i + 1) * 1000], nodes2, mode))
    return torch.cat(dist, dim=0)


def mse_loss(model_dist, true_dist, weighted_method, hyperbolic=False):
    assert model_dist.shape == true_dist.shape
    if weighted_method == 'ols':
        return ((model_dist - true_dist) ** 2).mean()
    elif weighted_method == 'fm':
        weight = 1 / (true_dist + 1e-5) ** 2
        return ((model_dist - true_dist) ** 2 * weight).mean()
    elif weighted_method == 'be':
        weight = 1 / (true_dist + 1e-5)
        return ((model_dist - true_dist) ** 2 * weight).mean()
    elif weighted_method == 'square_root_fm':
        weight = 1 / (true_dist + 1e-5) ** 2
        true_dist = torch.sqrt(true_dist)
        # if hyperbolic:
        #     # breakpoint()
        #     true_dist = true_dist ** 2
        #     weight = 1 / (torch.acosh(true_dist) + 1e-4) ** 2
        return ((model_dist - true_dist) ** 2 * weight).mean()
    elif weighted_method == 'square_root_be':
        true_dist = torch.sqrt(true_dist)
        weight = 1 / (true_dist + 1e-5)
        return ((model_dist - true_dist) ** 2 * weight).mean()
    elif weighted_method == 'square_root_ols':
        true_dist = torch.sqrt(true_dist)
        weight = 1
        return ((model_dist - true_dist) ** 2 * weight).mean()
    elif weighted_method == 'square_root_sqrt':
        true_dist = torch.sqrt(true_dist)
        weight = 1 / (torch.sqrt(true_dist) + 1e-5)
        return ((model_dist - true_dist) ** 2 * weight).mean()
    elif weighted_method == 'square_root_four':
        true_dist = torch.sqrt(true_dist)
        weight = 1 / (true_dist + 1e-5) ** 4
        return ((model_dist - true_dist) ** 2 * weight).mean()


def process_seq(self_seq, args, isbackbone):
    L = len(list(self_seq.values())[0])
    seq_tmp = {}
    raw_seqs = []
    ks = []
    if args.replicate_seq and (isbackbone or args.query_dist):
        for k in self_seq:
            seq_tmp[k.split('_')[0]] = torch.zeros(4, L)
    for k in self_seq:
        seq = np.zeros([4, L])
        raw_seq = np.array(self_seq[k])
        raw_seqs.append(raw_seq.reshape(1, -1))
        ks.append(k)
        seq[0][raw_seq == 'A'] = 1
        seq[1][raw_seq == 'C'] = 1
        seq[2][raw_seq == 'G'] = 1
        seq[3][raw_seq == 'T'] = 1
        seq[:, raw_seq == '-'] = args.gap_encode
        if args.replicate_seq and (isbackbone or args.query_dist):
            seq_tmp[k.split('_')[0]] += torch.from_numpy(seq)
        else:
            seq_tmp[k] = torch.from_numpy(seq)
    if args.replicate_seq and (isbackbone or args.query_dist):
        for k in seq_tmp:
            seq_tmp[k] = seq_tmp[k].float() / (seq_tmp[k].sum(dim=0, keepdim=True) + 1e-8)
    names = []
    seqs = []
    for k in seq_tmp:
        names.append(k)
        seqs.append(seq_tmp[k].unsqueeze(0))
    if args.jc_correct:
        return names, ks, torch.cat(seqs, dim=0), np.concatenate(raw_seqs, axis=0)
    return names, torch.cat(seqs, dim=0)


def save_depp_dist(model, args):
    print('processing data...')
    backbone_seq_file = args.backbone_seq_file
    query_seq_file = args.query_seq_file
    dis_file_root = os.path.join(args.outdir)
    # args.distance_ratio = float(1.0 / float(args.embedding_size) / 10 * float(args.distance_alpha))
    args.distance_ratio = model.hparams.distance_ratio
    args.gap_encode = model.hparams.gap_encode
    args.jc_correct = model.hparams.jc_correct
    args.distance_mode = model.hparams.distance_mode
    print('jc_correct', args.jc_correct)
    if args.jc_correct:
        args.jc_ratio = model.hparams.jc_ratio
    if not os.path.exists(dis_file_root):
        os.makedirs(dis_file_root)

    backbone_seq = SeqIO.to_dict(SeqIO.parse(backbone_seq_file, "fasta"))
    query_seq = SeqIO.to_dict(SeqIO.parse(query_seq_file, "fasta"))

    if args.jc_correct:
        backbone_seq_names, backbone_seq_names_raw, backbone_seq_tensor, backbone_raw_array = \
            process_seq(backbone_seq, args, isbackbone=True)
        query_seq_names, query_seq_names_raw, query_seq_tensor, query_raw_array = \
            process_seq(query_seq, args, isbackbone=False)
    else:
        # breakpoint()
        backbone_seq_names, backbone_seq_tensor = process_seq(backbone_seq, args, isbackbone=True)
        query_seq_names, query_seq_tensor = process_seq(query_seq, args, isbackbone=False)

    for param in model.parameters():
        param.requires_grad = False
    print('finish data processing!')
    print(f'{len(backbone_seq_names)} backbone sequences')
    print(f'{len(query_seq_names)} query sequence(s)')
    print(f'calculating embeddings...')
    backbone_encodings = []
    for i in range(math.ceil(len(backbone_seq_tensor) / 2000.0)):
        input_names = [j.split('_')[0] for j in backbone_seq_names[i * 2000: (i + 1) * 2000]]
        encodings_tmp = model(input_names).detach()
        backbone_encodings.append(encodings_tmp)
    backbone_encodings = torch.cat(backbone_encodings, dim=0)

    query_encodings = []
    for i in range(math.ceil(len(query_seq_tensor) / 2000.0)):
        input_names = [j.split('_')[0] for j in query_seq_names[i * 2000: (i + 1) * 2000]]
        encodings_tmp = model(input_names).detach()
        query_encodings.append(encodings_tmp)
    query_encodings = torch.cat(query_encodings, dim=0)
    print(f'finish embedding calculation!')

    torch.save(backbone_encodings, f'{args.outdir}/backbone_encodings.pt')
    torch.save(query_encodings, f'{args.outdir}/query_encodings.pt')
    torch.save(backbone_seq_names, f'{args.outdir}/backbone_names.pt')
    torch.save(query_seq_names, f'{args.outdir}/query_names.pt')

    query_dist = distance(query_encodings, backbone_encodings, args.distance_mode)

    if not args.distance_mode == 'hyperbolic':
        query_dist = query_dist * args.distance_ratio
    else:
        query_dist = query_dist * model.c

    # breakpoint()
    if args.jc_correct:
        jc_query_dist = jc_dist(query_raw_array, backbone_raw_array, query_seq_names_raw, backbone_seq_names_raw)
        jc_query_dist = jc_query_dist.groupby(by=lambda x: x.split('_')[0], axis=1).min()
        if args.query_dist:
            jc_query_dist = jc_query_dist.groupby(by=lambda x: x.split('_')[0], axis=0).min()
        jc_query_dist = jc_query_dist.loc[query_seq_names][backbone_seq_names]
        query_dist += args.jc_ratio * jc_query_dist.values

    query_dist = np.array(query_dist)
    query_dist[query_dist < 1e-3] = 0
    # if model.hparams.distance_mode == 'hyperbolic':
    #     print('model k', model.k)
    #     query_dist *= model.k
    if model.hparams.weighted_method in ['square_root_fm', 'square_root_be']:
        data_origin = dict(zip(query_seq_names, list(query_dist ** 2)))
    else:
        data_origin = dict(zip(query_seq_names, list(query_dist)))

    data_origin = pd.DataFrame.from_dict(data_origin, orient='index', columns=backbone_seq_names)

    # if args.query_dist:
    #     idx = data_origin.index
    #     data_origin = data_origin[idx]

    data_origin.to_csv(os.path.join(dis_file_root, f'depp.csv'), sep='\t')
    if not os.path.isdir(f'{args.outdir}/depp_tmp'):
        os.makedirs(f'{args.outdir}/depp_tmp')
    with open(f'{args.outdir}/depp_tmp/seq_name.txt', 'w') as f:
        f.write("\n".join(query_seq_names) + '\n')
    print('original distanace matrix saved!')


class Distance(Function):
    # @staticmethod
    # def grad(x, v, sqnormx, sqnormv, sqdist, eps):
    #     alpha = (1 - sqnormx)
    #     beta = (1 - sqnormv)
    #     z = 1 + 2 * sqdist / (alpha * beta)
    #     a = ((sqnormv - 2 * torch.sum(x * v, dim=-1) + 1) / torch.pow(alpha, 2))\
    #         .unsqueeze(-1).expand_as(x)
    #     a = a * x - v / alpha.unsqueeze(-1).expand_as(v)
    #     z = torch.sqrt(torch.pow(z, 2) - 1)
    #     z = torch.clamp(z * beta, min=eps).unsqueeze(-1)
    #     d = 4 * a / z.expand_as(x)
    #     d_p = ((1 - sqnormx) ** 2 / 4).unsqueeze(-1) * d
    #     return d_p

    @staticmethod
    def grad(x, v, sqnormx, sqnormv, sqdist, eps):
        alpha = (1 - sqnormx)
        beta = (1 - sqnormv)
        z = 1 + 2 * sqdist / (alpha * beta)
        # print('z.shape', z.shape)
        a = (sqnormv - 2 * torch.sum(x * v, dim=-1) + 1) / torch.pow(alpha, 2)
        # print('a.shape', a.shape)
        # print('v.shape', v.shape, alpha.shape)
        # breakpoint()
        a = a.unsqueeze(-1) * x - v / alpha.unsqueeze(-1)
        z = torch.sqrt(torch.pow(z, 2) - 1)
        z = torch.clamp(z * beta, min=eps)
        d = 4 * a / z.unsqueeze(-1)
        # print('d.shaped', d.shape)
        d_p = ((1 - sqnormx) ** 2 / 4).unsqueeze(-1) * d
        return d_p

    @staticmethod
    def forward(ctx, u, v, eps):
        # breakpoint()
        squnorm = torch.clamp(torch.sum(u * u, dim=-1), 0, 1 - eps)
        sqvnorm = torch.clamp(torch.sum(v * v, dim=-1), 0, 1 - eps)
        sqdist = torch.sum(torch.pow(u - v, 2), dim=-1)
        ctx.eps = eps
        ctx.save_for_backward(u, v, squnorm, sqvnorm, sqdist)
        x = sqdist / ((1 - squnorm) * (1 - sqvnorm)) * 2 + 1
        # arcosh
        z = torch.sqrt(torch.pow(x, 2) - 1)
        return torch.log(x + z)

    @staticmethod
    def backward(ctx, g):
        u, v, squnorm, sqvnorm, sqdist = ctx.saved_tensors
        g = g.unsqueeze(-1)
        gu = Distance.grad(u, v, squnorm, sqvnorm, sqdist, ctx.eps)
        gv = Distance.grad(v, u, sqvnorm, squnorm, sqdist, ctx.eps)
        return g.expand_as(gu) * gu, g.expand_as(gv) * gv, None


def mobius_linear(
        input,
        weight,
        bias=None,
        hyperbolic_input=True,
        hyperbolic_bias=True,
        nonlin=None,
        c=1.0,
):
    if hyperbolic_input:
        output = pmath.mobius_matvec(weight, input, k=-c)
    else:
        output = torch.nn.functional.linear(input, weight)
        output = pmath.expmap0(output, k=-c)
    if bias is not None:
        if not hyperbolic_bias:
            bias = pmath.expmap0(bias, k=-c)
        output = pmath.mobius_add(output, bias, k=-c)
    if nonlin is not None:
        output = pmath.mobius_fn_apply(nonlin, output, k=-c)
    output = pmath.project(output, k=-c)
    return output


class MobiusLinear(torch.nn.Linear):
    def __init__(
            self,
            *args,
            hyperbolic_input=True,
            hyperbolic_bias=True,
            nonlin=None,
            c=1.0,
            **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.ball = manifold = geoopt.PoincareBall(c=c)
        if self.bias is not None:
            if hyperbolic_bias:
                self.ball = manifold = geoopt.PoincareBall(c=c)
                self.bias = geoopt.ManifoldParameter(self.bias, manifold=manifold)
                with torch.no_grad():
                    self.bias.set_(pmath.expmap0(self.bias.normal_() / 4, c=c))
        with torch.no_grad():
            self.weight.normal_(std=1e-2)
        self.hyperbolic_bias = hyperbolic_bias
        self.hyperbolic_input = hyperbolic_input
        self.nonlin = nonlin

    def forward(self, input):
        return mobius_linear(
            input,
            weight=self.weight,
            bias=self.bias,
            hyperbolic_input=self.hyperbolic_input,
            nonlin=self.nonlin,
            hyperbolic_bias=self.hyperbolic_bias,
            c=self.ball.c,
        )

    def extra_repr(self):
        info = super().extra_repr()
        info += "c={}, hyperbolic_input={}".format(self.ball.c, self.hyperbolic_input)
        if self.bias is not None:
            info = ", hyperbolic_bias={}".format(self.hyperbolic_bias)
        return info
