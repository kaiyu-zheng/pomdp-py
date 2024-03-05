#!/bin/bash
# Example usage:
#
#   ./release.sh 1.3.3

# Define the function.
find_pxd_files_and_write_manifest() {
    dir=$1
    output_file=$2
    find "$dir" -name '*.pxd'| while read -r line; do
        echo "include $line"
    done > "$output_file"
    find "$dir" -name '*.pyx'| while read -r line; do
        echo "include $line"
    done >> "$output_file"
}

# Define the function.
is_git_repo_on_branch() {
    repo_path=$1
    branch_name=$2

    # Move to the git repository
    cd "$repo_path" || return 1

    # Get the current branch name
    current_branch=$(git rev-parse --abbrev-ref HEAD)

    # Check if the current branch is the expected one
    if [ "$current_branch" = "$branch_name" ]; then
        true && return
    else
        false
    fi
}

does_docker_image_exist() {
    image_name=$1

    # Check if the Docker image exists locally
    if [[ "$(docker images -q "$image_name" 2> /dev/null)" == "" ]]; then
        # If the image doesn't exist locally, pull it
        false
    else
        true && return
    fi
}

get_python_version() {
    version_string=$(python3 --version 2>&1)
    version=$(echo "$version_string" | awk '{print $2}')
    major_version=${version%%.*}
    minor_version=$(echo "$version" | cut -d. -f2)
    echo "cp${major_version}${minor_version}"
}

user_pwd=$PWD

# Write the MANIFEST.in file
pomdp_py_path=$HOME/repo/pomdp-py
cd $pomdp_py_path
find_pxd_files_and_write_manifest ./ MANIFEST.in

# Check if pomdp-py is on the right branch
if [ $# -ne 1 ]; then
    echo -e "Usage: $0 <version>\n"
    echo -e "Help:"
    echo -e "   version: version of pomdp-py release, e.g. 1.3.3"
    exit 1
fi

version=$1
if ! is_git_repo_on_branch $pomdp_py_path dev-$version; then
   echo "pomdp-py repo is not on the branch dev-$version that you want release for. Abort."
   exit 1
fi
echo -e "========= making release for pomdp-py $version ========="

pip install setuptools
pip install Cython

# Note that we are building with pyproject.toml
python3 setup.py build_ext --inplace
pip install build
python -m build

# create the manylinux container
linux_dist=manylinux2014_x86_64
manylinux_image=quay.io/pypa/$linux_dist
if ! does_docker_image_exist $manylinux_image; then
   docker pull $manylinux_image
fi
cpv=$(get_python_version)
wheel_name="pomdp_py-$version-$cpv-${cpv}-linux_x86_64.whl"
command="auditwheel repair io/dist/${wheel_name} -w /io/wheelhouse/"
docker run -it --mount type=bind,source=${pomdp_py_path},target=/io $manylinux_image  bash -c "$command"
rm $pomdp_py_path/dist/$wheel_name
fixed_wheel_name="pomdp_py-${version}-${cpv}-${cpv}-manylinux_2_17_x86_64.$linux_dist.whl"
sudo chown -R $(whoami).$(whoami) "$pomdp_py_path/wheelhouse/"
mv "$pomdp_py_path/wheelhouse/$fixed_wheel_name" "$pomdp_py_path/dist/$fixed_wheel_name"
rm -r $pomdp_py_path/wheelhouse

# Verification (wheel)
echo -e "------------ verification: wheel ---------"
pip uninstall pomdp_py
pip install "$pomdp_py_path/dist/$fixed_wheel_name"
python $pomdp_py_path/tests/test_all.py

# Verification (source)
echo -e "------------ verification: source ---------"
pip uninstall pomdp_py
cd $pomdp_py_path/dist
pip install pomdp-py-$version.tar.gz
python $pomdp_py_path/tests/test_all.py

pip install twine
echo -e "If successful, run"
echo -e "    python3 -m twine upload --repository pypi $pomdp_py_path/dist/*"
echo -e "to upload the release to PyPI."


cd $user_pwd
